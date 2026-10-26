"""Budget Dashboard — Dash entry point.

Run with ``uv run python -m Budget.app`` (not ``python Budget/app.py``):
module execution puts the repo root on ``sys.path`` so the package-qualified
imports below (``CreditCard.xxx``, ``Budget.budgeting``) resolve correctly.
Plain script execution would put ``Budget/`` itself on ``sys.path[0]``
instead, breaking those imports.
"""

import base64

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, callback_context, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate

from Budget.budgeting import (
    BUDGET_CATEGORIES,
    NON_CARD_CATEGORIES,
    PRESETS,
    RECENT_WINDOW_DAYS,
    budget_summary,
    budget_vs_actual,
    filter_to_recent_window,
    net_income,
    relabel_for_budget,
    suggested_allocations,
)
from Budget.export import budget_to_csv, budget_to_pdf
from Budget.statement import MAX_UPLOAD_BYTES, read_statement_csv
from CreditCard.analytics import needs_vs_wants, with_classification
from CreditCard.categorize import CATEGORIES, categorize_transactions, load_overrides, save_override
from CreditCard.cleaning import clean_statement

STATUS_COLOR = {"Under": "success", "Near": "warning", "Over": "danger", "No Budget": "secondary"}

# Needs/Wants/Other display label + categorical color (validated palette
# slots 1/2/3 — blue/orange/aqua), in NEEDS_WANTS_MAP's fixed order so the
# bar's segment order and the legend order below it always match.
NEEDS_WANTS_LABELS = {"Essential": "Needs", "Discretionary": "Wants", "Other": "Other"}
NEEDS_WANTS_COLORS = {"Essential": "#2a78d6", "Discretionary": "#eb6834", "Other": "#1baf7a"}

TABLE_STYLE = {
    "style_table": {"overflowX": "auto", "border": "1px solid #e6e9f2", "borderRadius": "12px"},
    "style_header": {
        "backgroundColor": "#f7f8fc",
        "color": "#6b7489",
        "fontWeight": "600",
        "fontSize": "12px",
        "textTransform": "uppercase",
        "letterSpacing": "0.04em",
        "border": "none",
        "borderBottom": "1px solid #e6e9f2",
        "padding": "12px 16px",
    },
    "style_cell": {
        "fontFamily": "Inter, sans-serif",
        "fontSize": "14px",
        "padding": "10px 16px",
        "border": "none",
        "borderBottom": "1px solid #f0f2f8",
        "textAlign": "left",
    },
    "style_data_conditional": [
        {"if": {"state": "active"}, "backgroundColor": "#eef1ff", "border": "1px solid #3b5bdb"},
    ],
}
MONEY = {"specifier": "$,.2f"}
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "Budget Studio"


def icon(name: str, className: str = "") -> html.I:
    """A Bootstrap Icon glyph, e.g. icon('cash-coin') — used in place of emoji throughout."""
    return html.I(className=f"bi bi-{name} {className}".strip())


# Number inputs deliberately have no HTML min/max: Dash reports an out-of-range
# value as None, which would hide it from these checks instead of flagging it.
def _is_negative(value) -> bool:
    return value is not None and value < 0


def _is_valid_pct(value) -> bool:
    return value is None or 0 <= value <= 100


def _manual_actuals(values: list | None) -> dict[str, float]:
    """Map the manual-actual inputs (laid out in ``NON_CARD_CATEGORIES`` order) to {category: $}.

    Negative entries are flagged invalid in the UI and counted as $0 here.
    """
    return {category: max(0.0, float(value or 0)) for category, value in zip(NON_CARD_CATEGORIES, values or [])}


def _clean_allocations(table_data: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(table_data) if table_data else pd.DataFrame(columns=["Category", "Budgeted Amount"])
    df["Budgeted Amount"] = pd.to_numeric(df.get("Budgeted Amount"), errors="coerce").fillna(0.0)
    return df


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _money(value: float, decimals: int = 2) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.{decimals}f}"


def build_category_review(df: pd.DataFrame) -> tuple:
    """List every merchant with its resolved category and a dropdown to fix it.

    Not just the ones flagged "Uncategorized" — a keyword rule or a stale
    override can also categorize a merchant *wrong*, and the user should be
    able to correct that too. Each dropdown defaults to the merchant's
    current category (not "Uncategorized"), so changing it is an explicit,
    deliberate correction. Returns ``(component, baseline)`` where
    ``baseline`` is a ``{merchant: category}`` snapshot used by the
    recategorize callback to tell "user changed this" apart from "dropdown
    just mounted at its current value" — only the former should write an
    override.
    """
    if df.empty:
        return html.Div(), {}

    baseline = df.drop_duplicates(subset="Description", keep="last").set_index("Description")["Category"].to_dict()
    stats = df.groupby("Description")["Amount"].agg(["count", "sum"])
    uncategorized_count = sum(1 for category in baseline.values() if category == "Uncategorized")
    merchants_sorted = sorted(baseline, key=lambda m: (baseline[m] != "Uncategorized", m.lower()))

    rows = [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Div(
                            [
                                merchant,
                                html.Span("Needs review", className="status-chip status-Near")
                                if baseline[merchant] == "Uncategorized"
                                else None,
                            ],
                            className="review-merchant",
                        ),
                        html.Div(
                            f"{_plural(int(stats.at[merchant, 'count']), 'transaction')} · "
                            f"{_money(stats.at[merchant, 'sum'])}",
                            className="note",
                        ),
                    ],
                    md=6,
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id={"type": "merchant-cat", "index": merchant},
                        options=[{"label": c, "value": c} for c in CATEGORIES],
                        value=baseline[merchant],
                        clearable=False,
                        style={"width": "100%"},
                    ),
                    md=6,
                    className="d-flex align-items-center",
                ),
            ],
            className="review-row",
        )
        for merchant in merchants_sorted
    ]

    toggle_label = (
        f"Review categories · {uncategorized_count} uncategorized of {_plural(len(merchants_sorted), 'merchant')}"
    )
    component = html.Div(
        [
            dbc.Button(
                [icon("tags", "me-2"), toggle_label],
                id="category-review-toggle",
                color="link",
                className="p-0 mt-3",
            ),
            dbc.Collapse(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.P(
                                "Change a merchant's category below if it's wrong — the fix is "
                                "remembered next time this merchant shows up.",
                                className="section-sub",
                            ),
                            *rows,
                        ]
                    ),
                    className="mt-2 review-card",
                ),
                id="category-review-collapse",
                is_open=uncategorized_count > 0,
            ),
        ]
    )
    return component, baseline


def empty_state(glyph: str, title: str, detail: str) -> html.Div:
    return html.Div(
        [icon(glyph), html.Div(title, className="title"), html.Div(detail, className="small")],
        className="empty-state",
    )


def build_progress_row(row: pd.Series):
    pct, actual, budgeted, status = row["Pct Used"], row["Actual Amount"], row["Budgeted Amount"], row["Status"]
    display_pct = min(pct, 100) if pct is not None else (100 if actual > 0 else 0)
    remaining = budgeted - actual
    if budgeted == 0:
        remaining_label = html.Span("not budgeted", className="text-muted")
    elif remaining >= 0:
        remaining_label = html.Span(f"{_money(remaining, 0)} left", className="text-muted")
    else:
        remaining_label = html.Span(f"{_money(-remaining, 0)} over", className="text-danger fw-semibold")
    return html.Div(
        [
            html.Div(
                [
                    html.Span(
                        [
                            row["Category"],
                            html.Span(status, className=f"status-chip status-{status.replace(' ', '')}"),
                        ],
                        className="name",
                    ),
                    html.Span([html.B(_money(actual, 0)), f" of {_money(budgeted, 0)}"], className="amounts"),
                ],
                className="d-flex justify-content-between align-items-center",
            ),
            dbc.Progress(value=display_pct, color=STATUS_COLOR.get(status, "secondary")),
            html.Div(
                [
                    html.Span("Manually entered" if row["Category"] in NON_CARD_CATEGORIES else ""),
                    remaining_label,
                ],
                className="note d-flex justify-content-between",
            ),
        ],
        className="cat-row",
    )


def build_bva_summary(result: pd.DataFrame) -> html.Div:
    """Spent / Budgeted / Remaining for the categories shown, plus an over-budget count."""
    budgeted, actual = result["Budgeted Amount"].sum(), result["Actual Amount"].sum()
    remaining = budgeted - actual
    over = int((result["Status"] == "Over").sum())
    over_chip = (
        html.Span([icon("exclamation-triangle-fill", "me-1"), f"{_plural(over, 'category', 'categories')} over budget"],
                  className="status-chip status-Over ms-0")
        if over
        else html.Span([icon("check-circle-fill", "me-1"), "All categories within budget"],
                       className="status-chip status-Under ms-0")
    )

    def stat(label, value, color=None):
        return html.Div(
            [html.Div(label, className="k"), html.Div(_money(value), className="v", style={"color": color})],
            className="alloc-stat",
        )

    return html.Div(
        [
            html.Div(
                [
                    stat("Spent", actual),
                    stat("Budgeted", budgeted),
                    stat("Remaining", remaining, "#e03131" if remaining < 0 else "#1baf7a"),
                    html.Div(over_chip, className="d-flex align-items-end pb-1"),
                ],
                className="alloc-stats",
            ),
            html.Div(
                html.Div(
                    style={
                        "width": f"{min(100.0, actual / budgeted * 100) if budgeted else 0:.1f}%",
                        "background": "#e03131" if remaining < 0 else "#3b5bdb",
                    }
                ),
                className="alloc-meter mb-4",
            ),
        ]
    )


def build_budget_chart(result: pd.DataFrame):
    # ``result`` arrives sorted largest-first to match the progress rows; Plotly
    # draws the first horizontal category at the bottom, so reverse it here.
    order = list(result["Category"])[::-1]
    fig = go.Figure()
    fig.add_bar(
        name="Budgeted",
        y=result["Category"],
        x=result["Budgeted Amount"],
        orientation="h",
        marker={"color": "#c5cff5"},
        hovertemplate="%{y}<br>Budgeted: $%{x:,.2f}<extra></extra>",
    )
    fig.add_bar(
        name="Actual",
        y=result["Category"],
        x=result["Actual Amount"],
        orientation="h",
        marker={"color": "#3b5bdb"},
        hovertemplate="%{y}<br>Actual: $%{x:,.2f}<extra></extra>",
    )
    fig.update_layout(
        barmode="group",
        bargap=0.35,
        height=max(360, 44 * len(result)),
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        legend={"orientation": "h", "y": 1.04, "x": 1, "xanchor": "right"},
        yaxis={"categoryorder": "array", "categoryarray": order, "automargin": True},
        xaxis={"tickprefix": "$", "tickformat": ",.0f", "gridcolor": "#eef0f7", "zeroline": False},
        font={"family": "Inter, sans-serif", "color": "#1b2437", "size": 12},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"font_family": "Inter, sans-serif"},
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def build_needs_wants_section(transactions_df: pd.DataFrame | None):
    """A single full-width horizontal stacked bar for the Needs/Wants/Other split.

    Part-to-whole across 3 categories reads more clearly as one stacked bar
    than a donut — there's no angle-vs-angle comparison for the reader to
    make, and a segment's width scales linearly with its share. Exact $/%
    are given as a legend row below rather than text baked into the bar,
    since a segment (e.g. a small "Other") can be too narrow to hold a label.
    """
    if transactions_df is None or transactions_df.empty:
        return html.Div()
    summary = needs_vs_wants(transactions_df)

    fig = go.Figure()
    for row in summary.itertuples():
        label = NEEDS_WANTS_LABELS[row.Classification]
        fig.add_bar(
            name=label,
            y=["Actual Spend"],
            x=[row.Amount],
            orientation="h",
            marker={"color": NEEDS_WANTS_COLORS[row.Classification], "line": {"color": "#ffffff", "width": 2}},
            hovertemplate=f"{label}: $%{{x:,.2f}} ({row.pct_of_total:.1f}%)<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        bargap=0,
        height=90,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        showlegend=False,
        xaxis={"visible": False, "range": [0, float(summary["Amount"].sum())]},
        yaxis={"visible": False},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )

    legend_chips = [
        html.Span(
            [
                html.Span(
                    className="d-inline-block me-1",
                    style={
                        "width": "10px",
                        "height": "10px",
                        "borderRadius": "2px",
                        "backgroundColor": NEEDS_WANTS_COLORS[row.Classification],
                    },
                ),
                f"{NEEDS_WANTS_LABELS[row.Classification]}: ${row.Amount:,.0f} ({row.pct_of_total:.0f}%)",
            ],
            className="d-inline-flex align-items-center mx-3 legend-chip",
        )
        for row in summary.itertuples()
    ]

    return html.Div(
        [
            html.Hr(className="my-4"),
            html.Div("Card spending: Needs vs. Wants vs. Other", className="subsection-title text-center"),
            dcc.Graph(figure=fig, config={"displayModeBar": False}),
            html.Div(legend_chips, className="d-flex justify-content-center flex-wrap mb-2"),
        ],
        className="mt-2",
    )


def build_drilldown_accordion(result: pd.DataFrame, transactions_df: pd.DataFrame | None):
    if transactions_df is None or transactions_df.empty:
        return html.Div()
    # Classify using CreditCard's raw category names first, then relabel
    # ("Shopping" -> "Misc Shopping") so filtering against `result`'s
    # Budget-labeled categories below actually matches.
    classified = relabel_for_budget(with_classification(transactions_df))
    classified["Transaction Date"] = pd.to_datetime(classified["Transaction Date"])
    classified["Classification"] = classified["Classification"].map(NEEDS_WANTS_LABELS)
    items = []
    for category in result.loc[result["Actual Amount"] > 0, "Category"]:
        if category in NON_CARD_CATEGORIES:
            continue  # manually entered, no underlying transactions to drill into
        subset = classified.loc[
            classified["Category"] == category, ["Transaction Date", "Description", "Amount", "Classification"]
        ].sort_values("Transaction Date", ascending=False)
        total = subset["Amount"].sum()
        subset = subset.assign(**{"Transaction Date": subset["Transaction Date"].dt.strftime("%b %d, %Y")})
        table = dash_table.DataTable(
            data=subset.rename(columns={"Transaction Date": "Date", "Classification": "Type"}).to_dict("records"),
            columns=[
                {"name": "Date", "id": "Date"},
                {"name": "Merchant", "id": "Description"},
                {"name": "Amount", "id": "Amount", "type": "numeric", "format": MONEY},
                {"name": "Type", "id": "Type"},
            ],
            page_size=10,
            **{
                **TABLE_STYLE,
                "style_cell_conditional": [{"if": {"column_id": "Amount"}, "textAlign": "right"}],
            },
        )
        title = html.Div(
            [html.Span(category), html.Span(f"{_plural(len(subset), 'transaction')} · {_money(total)}",
                                            className="section-sub ms-auto me-3")],
            className="d-flex w-100 align-items-center",
        )
        items.append(dbc.AccordionItem(table, title=title))
    if not items:
        return html.Div()
    return html.Div(
        [
            html.Div("Transactions by category", className="subsection-title mt-4 mb-2"),
            dbc.Accordion(items, start_collapsed=True),
        ]
    )


def card_header(step: int, glyph: str, title: str, subtitle: str = "", extra=None) -> dbc.CardHeader:
    return dbc.CardHeader(
        [
            html.Span(str(step), className="step-pill"),
            html.Div(
                [html.Div([icon(glyph, "me-2 text-muted"), title]), html.Div(subtitle, className="section-sub")]
                if subtitle
                else [icon(glyph, "me-2 text-muted"), title],
                className="header-title",
            ),
            html.Div(extra, className="header-extra") if extra is not None else None,
        ]
    )


app.layout = html.Div(
    [
        html.Div(
            dbc.Container(
                html.Div(
                    [
                        html.Span(icon("piggy-bank"), className="brand-mark"),
                        html.Div(
                            [
                                html.H1("Budget Studio"),
                                html.P("Build a budget from a preset, then see how your real spending compares."),
                            ]
                        ),
                    ],
                    className="d-flex align-items-center",
                ),
                className="app-shell",
                style={"margin": "0 auto"},
            ),
            className="app-header",
        ),
        html.Div(
            [
                dcc.Store(id="cleaned-transactions-store"),
                dcc.Store(id="transactions-store"),
                dcc.Store(id="category-baseline-store"),
                dbc.Card(
                    [
                        card_header(
                            1, "cash-coin", "Income & Method", "Tell us what you earn and how you like to budget"
                        ),
                        dbc.CardBody(
                            [
                                dbc.Row(
                                    [
                                        dbc.Col(
                                            [
                                                dbc.Label(["Monthly Income ", html.Span("*", className="text-danger")]),
                                                dbc.InputGroup(
                                                    [
                                                        dbc.InputGroupText("$"),
                                                        dbc.Input(
                                                            id="income-amount", type="number", placeholder="5000"
                                                        ),
                                                        dbc.FormFeedback(
                                                            "Enter your monthly income to generate a budget.",
                                                            id="income-feedback",
                                                            type="invalid",
                                                        ),
                                                    ]
                                                ),
                                            ],
                                            md=4,
                                        ),
                                        dbc.Col(
                                            [
                                                dbc.Label("Income Type"),
                                                html.Div(
                                                    dbc.RadioItems(
                                                        id="income-type",
                                                        options=[
                                                            {"label": "Gross", "value": "Gross"},
                                                            {"label": "Net", "value": "Net"},
                                                        ],
                                                        value="Net",
                                                        inline=True,
                                                        className="pt-1",
                                                    ),
                                                ),
                                                html.Div(
                                                    "Choose Gross to enter pre-tax pay — you'll set an "
                                                    "estimated tax rate.",
                                                    className="preset-hint",
                                                ),
                                            ],
                                            md=4,
                                        ),
                                        dbc.Col(
                                            [
                                                dbc.Label("Budgeting Method"),
                                                dcc.Dropdown(
                                                    id="preset-dropdown",
                                                    options=[{"label": name, "value": name} for name in PRESETS],
                                                    value="50/30/20",
                                                    clearable=False,
                                                ),
                                                html.Div(id="preset-description", className="preset-hint"),
                                            ],
                                            md=4,
                                        ),
                                    ],
                                    className="g-4",
                                ),
                                dbc.Collapse(
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                [
                                                    dbc.Label("Estimated Tax Rate"),
                                                    dbc.InputGroup(
                                                        [
                                                            dbc.Input(
                                                                id="tax-rate-input",
                                                                type="number",
                                                                value=22,
                                                            ),
                                                            dbc.InputGroupText("%"),
                                                            dbc.FormFeedback(
                                                                "Enter a rate from 0 to 100.", type="invalid"
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        "Effective rate: federal + state + FICA.",
                                                        className="preset-hint",
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                            dbc.Col(
                                                [
                                                    dbc.Label("Net Monthly Income"),
                                                    html.Div(id="net-income-display", className="fs-5 fw-bold pt-1"),
                                                    html.Div(
                                                        "The budget is built on this take-home amount.",
                                                        className="preset-hint",
                                                    ),
                                                ],
                                                md=4,
                                            ),
                                        ],
                                        className="mt-2",
                                    ),
                                    id="tax-rate-collapse",
                                    is_open=False,
                                ),
                                dbc.Collapse(
                                    dbc.Row(
                                        dbc.Col(
                                            [
                                                dbc.Label("Savings Rate"),
                                                dbc.InputGroup(
                                                    [
                                                        dbc.Input(
                                                            id="savings-rate-input",
                                                            type="number",
                                                            value=20,
                                                        ),
                                                        dbc.InputGroupText("%"),
                                                        dbc.FormFeedback("Enter a rate from 0 to 100.", type="invalid"),
                                                    ]
                                                ),
                                            ],
                                            md=4,
                                        ),
                                        className="mt-2",
                                    ),
                                    id="savings-rate-collapse",
                                    is_open=False,
                                ),
                                html.Div(
                                    [
                                        dbc.Button(
                                            [icon("magic", "me-2"), "Generate Suggested Budget"],
                                            id="generate-budget-btn",
                                            color="primary",
                                        ),
                                        html.Span(
                                            "Replaces the amounts in step 2 — you can fine-tune them after.",
                                            className="section-sub",
                                        ),
                                    ],
                                    className="d-flex align-items-center flex-wrap gap-3 mt-3",
                                ),
                                dbc.Modal(
                                    [
                                        dbc.ModalHeader(dbc.ModalTitle("Replace your allocations?")),
                                        dbc.ModalBody(
                                            "Generating a suggested budget overwrites every amount in step 2, "
                                            "including any edits you've made. This can't be undone."
                                        ),
                                        dbc.ModalFooter(
                                            [
                                                dbc.Button("Keep my edits", id="generate-cancel-btn", color="light"),
                                                dbc.Button(
                                                    "Replace amounts", id="generate-confirm-btn", color="danger"
                                                ),
                                            ]
                                        ),
                                    ],
                                    id="generate-confirm-modal",
                                    is_open=False,
                                    centered=True,
                                ),
                            ]
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Card(
                    [
                        card_header(
                            2,
                            "sliders",
                            "Allocations",
                            "Edit any amount — everything below updates instantly",
                            extra=html.Div(
                                [
                                    html.Span("Export", className="section-sub me-2"),
                                    dbc.ButtonGroup(
                                        [
                                            dbc.Button(
                                                [icon("filetype-csv", "me-1"), "CSV"],
                                                id="save-csv-btn",
                                                outline=True,
                                                size="sm",
                                                color="primary",
                                            ),
                                            dbc.Button(
                                                [icon("filetype-pdf", "me-1"), "PDF"],
                                                id="save-pdf-btn",
                                                outline=True,
                                                size="sm",
                                                color="primary",
                                            ),
                                        ]
                                    ),
                                    dcc.Download(id="download-budget"),
                                ],
                                className="d-flex align-items-center",
                            ),
                        ),
                        dbc.CardBody(
                            [
                                html.Div(id="alloc-summary"),
                                dash_table.DataTable(
                                    id="allocations-table",
                                    data=[{"Category": c, "Budgeted Amount": 0.0} for c in BUDGET_CATEGORIES],
                                    columns=[
                                        {"name": "Category", "id": "Category", "editable": False},
                                        {
                                            "name": "Budgeted Amount",
                                            "id": "Budgeted Amount",
                                            "editable": True,
                                            "type": "numeric",
                                            "format": MONEY,
                                            # Typing text into an amount cell is rejected outright
                                            # instead of silently becoming $0.
                                            "on_change": {"action": "validate", "failure": "reject"},
                                        },
                                    ],
                                    editable=True,
                                    page_size=20,
                                    **{
                                        **TABLE_STYLE,
                                        "style_cell_conditional": [
                                            {"if": {"column_id": "Budgeted Amount"}, "textAlign": "right"},
                                            {"if": {"column_id": "Category"}, "fontWeight": "500"},
                                        ],
                                        "style_data_conditional": [
                                            *TABLE_STYLE["style_data_conditional"],
                                            {
                                                "if": {
                                                    "filter_query": "{Budgeted Amount} < 0",
                                                    "column_id": "Budgeted Amount",
                                                },
                                                "backgroundColor": "#fde4e4",
                                                "color": "#b42323",
                                                "fontWeight": "600",
                                            },
                                        ],
                                    },
                                ),
                            ]
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Card(
                    [
                        card_header(
                            3,
                            "upload",
                            "Upload Credit Card Statement",
                            "Expected CSV columns: Transaction Date, Description, Amount "
                            "(Type and Category are used when present)",
                        ),
                        dbc.CardBody(
                            [
                                dcc.Upload(
                                    id="upload-statement",
                                    children=html.Div(
                                        [
                                            icon("cloud-arrow-up"),
                                            html.Div("Drag & drop a statement CSV", className="title"),
                                            html.Div(
                                                f"or click to browse · CSV up to {MAX_UPLOAD_MB} MB",
                                                className="small",
                                            ),
                                        ]
                                    ),
                                    accept=".csv,text/csv",
                                    max_size=MAX_UPLOAD_BYTES,
                                    className="dropzone",
                                ),
                                dcc.Loading(
                                    html.Div(id="upload-alert", className="mt-3"), type="dot", color="#3b5bdb"
                                ),
                                html.Div(id="category-review"),
                            ]
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Card(
                    [
                        card_header(
                            4, "bar-chart-line", "Budget vs. Actual", "Your plan against the last 30 days of spending"
                        ),
                        dbc.CardBody(
                            [
                                html.Div(
                                    [
                                        html.Div(
                                            [icon("pencil-square", "me-2 text-muted"), "Manually tracked spending"],
                                            className="fw-semibold mb-1",
                                        ),
                                        html.Div(
                                            "These never appear on a card statement — enter what you actually "
                                            "paid this month, then press Enter or click away to apply.",
                                            className="section-sub mb-3",
                                        ),
                                        dbc.Row(
                                            [
                                                dbc.Col(
                                                    [
                                                        dbc.Label(category),
                                                        dbc.InputGroup(
                                                            [
                                                                dbc.InputGroupText("$"),
                                                                dbc.Input(
                                                                    id={"type": "manual-actual", "index": category},
                                                                    type="number",
                                                                    placeholder="0",
                                                                    debounce=True,
                                                                ),
                                                                dbc.FormFeedback("Can't be negative.", type="invalid"),
                                                            ]
                                                        ),
                                                    ],
                                                    md=4,
                                                )
                                                for category in NON_CARD_CATEGORIES
                                            ],
                                            className="g-3",
                                        ),
                                    ],
                                    className="manual-actuals mb-4 pb-4 border-bottom",
                                ),
                                dcc.Loading(html.Div(id="budget-vs-actual-section"), type="dot", color="#3b5bdb"),
                            ]
                        ),
                    ],
                    className="mb-4",
                ),
                html.Footer(
                    [
                        icon("shield-lock", "me-2"),
                        "Session-only: income, allocations and manual entries reset when the app restarts. "
                        "Only merchant category fixes are remembered.",
                    ],
                    className="app-footer",
                ),
            ],
            className="app-shell",
        ),
    ]
)


@app.callback(
    Output("download-budget", "data"),
    Input("save-csv-btn", "n_clicks"),
    Input("save-pdf-btn", "n_clicks"),
    State("allocations-table", "data"),
    State("income-amount", "value"),
    State("preset-dropdown", "value"),
    State("transactions-store", "data"),
    State("income-type", "value"),
    State("tax-rate-input", "value"),
    State({"type": "manual-actual", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def save_budget(
    csv_clicks, pdf_clicks, table_data, income, preset, transaction_records, income_type, tax_rate, manual_values
):
    triggered = callback_context.triggered_id
    if not (csv_clicks or pdf_clicks):
        raise PreventUpdate

    transactions_df = pd.DataFrame.from_records(transaction_records) if transaction_records else None
    manual_actuals = _manual_actuals(manual_values)
    result = budget_vs_actual(_clean_allocations(table_data), transactions_df, manual_actuals)
    has_actuals = transactions_df is not None or any(manual_actuals.values())
    gross_income = (income or 0) if income_type == "Gross" else None
    income = net_income(income or 0, income_type, tax_rate)
    tax_args = (gross_income, tax_rate)
    if triggered == "save-csv-btn":
        return dcc.send_string(budget_to_csv(income, preset, result, has_actuals, *tax_args), "budget.csv")
    return dcc.send_bytes(budget_to_pdf(income, preset, result, has_actuals, *tax_args), "budget.pdf")


@app.callback(
    Output("tax-rate-collapse", "is_open"),
    Output("net-income-display", "children"),
    Input("income-type", "value"),
    Input("income-amount", "value"),
    Input("tax-rate-input", "value"),
)
def update_tax_section(income_type, income, tax_rate):
    return income_type == "Gross", f"${net_income(income or 0, income_type, tax_rate):,.2f}"


@app.callback(
    Output("savings-rate-collapse", "is_open"),
    Output("preset-description", "children"),
    Input("preset-dropdown", "value"),
)
def update_preset_description(preset):
    return preset == "Pay Yourself First", PRESETS.get(preset, "")


@app.callback(
    Output("income-amount", "invalid"),
    Output("income-feedback", "children"),
    Input("generate-budget-btn", "n_clicks"),
    Input("income-amount", "value"),
)
def flag_income(n_clicks, income):
    if _is_negative(income):
        return True, "Income can't be negative."
    return bool(n_clicks) and not income, "Enter your monthly income to generate a budget."


@app.callback(
    Output("tax-rate-input", "invalid"),
    Output("savings-rate-input", "invalid"),
    Input("tax-rate-input", "value"),
    Input("savings-rate-input", "value"),
)
def flag_rates(tax_rate, savings_rate):
    return not _is_valid_pct(tax_rate), not _is_valid_pct(savings_rate)


@app.callback(
    Output({"type": "manual-actual", "index": ALL}, "invalid"),
    Input({"type": "manual-actual", "index": ALL}, "value"),
)
def flag_manual_actuals(values):
    return [_is_negative(v) for v in values]


_GENERATE_STATES = [
    State("income-amount", "value"),
    State("preset-dropdown", "value"),
    State("savings-rate-input", "value"),
    State("income-type", "value"),
    State("tax-rate-input", "value"),
]


def _generate_records(income, preset, savings_rate_pct, income_type, tax_rate) -> list[dict] | None:
    """Suggested allocations as table records, or ``None`` if the inputs aren't valid yet."""
    if not income or income < 0 or not _is_valid_pct(tax_rate) or not _is_valid_pct(savings_rate_pct):
        return None
    income = net_income(income, income_type, tax_rate)
    savings_rate = (20 if savings_rate_pct is None else savings_rate_pct) / 100
    return suggested_allocations(preset, income, savings_rate=savings_rate).to_dict("records")


@app.callback(
    Output("allocations-table", "data"),
    Output("generate-confirm-modal", "is_open"),
    Input("generate-budget-btn", "n_clicks"),
    *_GENERATE_STATES,
    State("allocations-table", "data"),
    prevent_initial_call=True,
)
def generate_budget(n_clicks, income, preset, savings_rate_pct, income_type, tax_rate, table_data):
    records = _generate_records(income, preset, savings_rate_pct, income_type, tax_rate)
    if records is None:
        raise PreventUpdate
    if _clean_allocations(table_data)["Budgeted Amount"].abs().sum() > 0:
        return no_update, True  # existing amounts — confirm before overwriting them
    return records, False


@app.callback(
    Output("allocations-table", "data", allow_duplicate=True),
    Output("generate-confirm-modal", "is_open", allow_duplicate=True),
    Input("generate-confirm-btn", "n_clicks"),
    *_GENERATE_STATES,
    prevent_initial_call=True,
)
def confirm_generate(n_clicks, income, preset, savings_rate_pct, income_type, tax_rate):
    records = _generate_records(income, preset, savings_rate_pct, income_type, tax_rate)
    return (records if records is not None else no_update), False


@app.callback(
    Output("generate-confirm-modal", "is_open", allow_duplicate=True),
    Input("generate-cancel-btn", "n_clicks"),
    prevent_initial_call=True,
)
def cancel_generate(n_clicks):
    return False


@app.callback(
    Output("alloc-summary", "children"),
    Input("allocations-table", "data"),
    Input("income-amount", "value"),
    Input("income-type", "value"),
    Input("tax-rate-input", "value"),
)
def update_allocation_summary(table_data, income, income_type, tax_rate):
    income = net_income(income or 0, income_type, tax_rate)
    summary = budget_summary(income, _clean_allocations(table_data))
    unallocated = summary["unallocated"]
    if abs(unallocated) < 1:
        color, status = "#1baf7a", html.Span([icon("check-circle-fill", "me-1"), "Every dollar assigned"],
                                             className="status-chip status-Under ms-0")
    elif unallocated < 0:
        color, status = "#e03131", html.Span([icon("exclamation-triangle-fill", "me-1"), "Over-allocated"],
                                             className="status-chip status-Over ms-0")
    else:
        color, status = "#f08c00", html.Span([icon("hourglass-split", "me-1"), "Left to assign"],
                                             className="status-chip status-Near ms-0")
    used_pct = min(100.0, summary["total_budgeted"] / income * 100) if income else 0

    def stat(label, value, extra_style=None):
        return html.Div(
            [html.Div(label, className="k"), html.Div(_money(value), className="v", style=extra_style)],
            className="alloc-stat",
        )

    stats = html.Div(
        [
            stat("Take-home income", income),
            stat("Budgeted", summary["total_budgeted"]),
            stat("Unallocated", unallocated, {"color": color}),
            html.Div(status, className="d-flex align-items-end pb-1") if income else None,
        ],
        className="alloc-stats",
    )
    meter = html.Div(html.Div(style={"width": f"{used_pct:.1f}%", "background": color}), className="alloc-meter mb-4")
    allocations = _clean_allocations(table_data)
    negative = allocations.loc[allocations["Budgeted Amount"] < 0, "Category"].tolist()
    warning = (
        dbc.Alert(
            [icon("exclamation-triangle-fill", "me-2"), f"Negative amounts in: {', '.join(negative)}."],
            color="danger",
            className="py-2",
        )
        if negative
        else None
    )
    return [stats, meter, warning]


@app.callback(
    Output("cleaned-transactions-store", "data"),
    Output("transactions-store", "data"),
    Output("upload-alert", "children"),
    Output("category-review", "children"),
    Output("category-baseline-store", "data"),
    Input("upload-statement", "contents"),
    State("upload-statement", "filename"),
    prevent_initial_call=True,
)
def handle_upload(contents, filename):
    if contents is None:
        raise PreventUpdate

    def error(message):
        alert = dbc.Alert([icon("x-circle-fill", "me-2"), message], color="danger", className="mb-0")
        return None, None, alert, html.Div(), None

    try:
        _, content_string = contents.split(",", 1)
        raw_df = read_statement_csv(base64.b64decode(content_string))
    except ValueError as exc:  # includes binascii.Error from a malformed payload
        return error(f"Couldn't read {filename or 'that file'}: {exc} Please upload a CSV exported from your card.")
    try:
        cleaned_df = clean_statement(raw_df)
    except ValueError as exc:
        return error(str(exc))

    total_count = len(cleaned_df)
    cleaned_df = filter_to_recent_window(cleaned_df)
    categorized_df = categorize_transactions(cleaned_df, overrides=load_overrides())

    message = f"{filename}: loaded {_plural(len(cleaned_df), 'transaction')} from the last {RECENT_WINDOW_DAYS} days"
    message += f" (filtered from {total_count} total)." if len(cleaned_df) != total_count else "."
    alert = dbc.Alert([icon("check-circle-fill", "me-2"), message], color="success", className="mb-0")
    review, baseline = build_category_review(categorized_df)
    return cleaned_df.to_dict("records"), categorized_df.to_dict("records"), alert, review, baseline


@app.callback(
    Output("category-review-collapse", "is_open"),
    Input("category-review-toggle", "n_clicks"),
    State("category-review-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_category_review(n_clicks, is_open):
    return not is_open


@app.callback(
    Output("transactions-store", "data", allow_duplicate=True),
    Output("category-review", "children", allow_duplicate=True),
    Output("category-baseline-store", "data", allow_duplicate=True),
    Input({"type": "merchant-cat", "index": ALL}, "value"),
    State("cleaned-transactions-store", "data"),
    State("category-baseline-store", "data"),
    prevent_initial_call=True,
)
def recategorize_merchants(selected_categories, cleaned_records, baseline):
    if not cleaned_records:
        raise PreventUpdate

    baseline = baseline or {}
    merchant_ids = [item["id"]["index"] for item in callback_context.inputs_list[0]]
    changed = False
    for merchant, chosen in zip(merchant_ids, selected_categories):
        if chosen and chosen != baseline.get(merchant):
            save_override(merchant, chosen)
            changed = True

    if not changed:
        raise PreventUpdate

    cleaned_df = pd.DataFrame.from_records(cleaned_records)
    categorized_df = categorize_transactions(cleaned_df, overrides=load_overrides())
    review, new_baseline = build_category_review(categorized_df)
    return categorized_df.to_dict("records"), review, new_baseline


@app.callback(
    Output("budget-vs-actual-section", "children"),
    Input("allocations-table", "data"),
    Input("transactions-store", "data"),
    Input({"type": "manual-actual", "index": ALL}, "value"),
)
def update_budget_vs_actual(table_data, transaction_records, manual_values):
    allocations_df = _clean_allocations(table_data)
    transactions_df = pd.DataFrame.from_records(transaction_records) if transaction_records else None
    result = budget_vs_actual(allocations_df, transactions_df, _manual_actuals(manual_values))
    # Hide categories with nothing budgeted and nothing spent — a $0-of-$0 row
    # says nothing — and sort largest-first so the rows and chart line up.
    result = result[(result["Budgeted Amount"] > 0) | (result["Actual Amount"] > 0)]
    result = result.assign(_size=result[["Budgeted Amount", "Actual Amount"]].max(axis=1))
    result = result.sort_values("_size", ascending=False).drop(columns="_size")

    if result.empty:
        return empty_state(
            "clipboard-data",
            "Nothing to compare yet",
            "Generate a budget in step 1 and upload a statement in step 3 to see how you're tracking.",
        )

    no_data_hint = (
        []
        if transactions_df is not None
        else [
            dbc.Alert(
                [
                    icon("info-circle", "me-2"),
                    "Upload a statement in step 3 to see your card spending against this plan.",
                ],
                color="light",
                className="border",
            )
        ]
    )
    return [
        *no_data_hint,
        build_bva_summary(result),
        dbc.Row(
            [
                dbc.Col([build_progress_row(row) for _, row in result.iterrows()], lg=6),
                dbc.Col(build_budget_chart(result), lg=6),
            ],
            className="g-4",
        ),
        build_needs_wants_section(transactions_df),
        build_drilldown_accordion(result, transactions_df),
    ]


if __name__ == "__main__":
    app.run(debug=True)
