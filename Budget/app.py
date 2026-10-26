"""Budget Dashboard — Dash entry point.

Run with ``uv run python -m Budget.app`` (not ``python Budget/app.py``):
module execution puts the repo root on ``sys.path`` so the package-qualified
imports below (``CreditCard.xxx``, ``Budget.budgeting``) resolve correctly.
Plain script execution would put ``Budget/`` itself on ``sys.path[0]``
instead, breaking those imports.
"""

import base64
import io

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, callback_context, dash_table, dcc, html
from dash.exceptions import PreventUpdate

from Budget.budgeting import (
    BUDGET_CATEGORIES,
    NON_CARD_CATEGORIES,
    PRESETS,
    RECENT_WINDOW_DAYS,
    budget_summary,
    budget_vs_actual,
    filter_to_recent_window,
    relabel_for_budget,
    suggested_allocations,
)
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

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "Budget Studio"


def icon(name: str, className: str = "") -> html.I:
    """A Bootstrap Icon glyph, e.g. icon('cash-coin') — used in place of emoji throughout."""
    return html.I(className=f"bi bi-{name} {className}".strip())


def _clean_allocations(table_data: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(table_data) if table_data else pd.DataFrame(columns=["Category", "Budgeted Amount"])
    df["Budgeted Amount"] = pd.to_numeric(df.get("Budgeted Amount"), errors="coerce").fillna(0.0)
    return df


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
    uncategorized_count = sum(1 for category in baseline.values() if category == "Uncategorized")
    merchants_sorted = sorted(baseline, key=lambda m: (baseline[m] != "Uncategorized", m.lower()))

    rows = [
        dbc.Row(
            [
                dbc.Col(
                    html.Div(merchant, className="review-merchant"),
                    md=6,
                    className="d-flex align-items-center",
                ),
                dbc.Col(
                    dcc.Dropdown(
                        id={"type": "merchant-cat", "index": merchant},
                        options=[{"label": c, "value": c} for c in CATEGORIES],
                        value=baseline[merchant],
                        clearable=False,
                    ),
                    md=6,
                ),
            ],
            className="mb-2",
        )
        for merchant in merchants_sorted
    ]

    toggle_label = f"Review categories ({uncategorized_count} uncategorized of {len(merchants_sorted)} merchant(s))"
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
                                className="text-muted small",
                            ),
                            *rows,
                        ]
                    ),
                    className="mt-2 bg-light",
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
    note = (
        html.Div("Manually tracked — not on your card statement", className="note")
        if row["Category"] in NON_CARD_CATEGORIES
        else None
    )
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
                    html.Span([html.B(f"${actual:,.0f}"), f" of ${budgeted:,.0f}"], className="amounts"),
                ],
                className="d-flex justify-content-between align-items-center",
            ),
            dbc.Progress(value=display_pct, color=STATUS_COLOR.get(status, "secondary")),
            note,
        ],
        className="cat-row",
    )


def build_budget_chart(result: pd.DataFrame):
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
        height=max(420, 34 * len(result)),
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        legend={"orientation": "h", "y": 1.04, "x": 1, "xanchor": "right"},
        yaxis={"categoryorder": "total ascending", "automargin": True},
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
            html.H6("Actual Spend: Needs vs. Wants vs. Other", className="text-center fw-semibold"),
            dcc.Graph(figure=fig, config={"displayModeBar": False}),
            html.Div(legend_chips, className="d-flex justify-content-center mb-2"),
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
    items = []
    for category in result.loc[result["Actual Amount"] > 0, "Category"]:
        subset = classified.loc[
            classified["Category"] == category, ["Transaction Date", "Description", "Amount", "Classification"]
        ]
        table = dash_table.DataTable(
            data=subset.to_dict("records"),
            columns=[
                {"name": c, "id": c, **({"type": "numeric", "format": MONEY} if c == "Amount" else {})}
                for c in subset.columns
            ],
            page_size=10,
            **TABLE_STYLE,
        )
        items.append(dbc.AccordionItem(table, title=f"{category} — {len(subset)} transaction(s)"))
    if not items:
        return html.Div()
    return dbc.Accordion(items, start_collapsed=True, className="mt-4", flush=False)


def card_header(step: int, glyph: str, title: str, subtitle: str = "", extra=None) -> dbc.CardHeader:
    return dbc.CardHeader(
        [
            html.Span(str(step), className="step-pill"),
            html.Div(
                [html.Div([icon(glyph, "me-2 text-muted"), title]), html.Div(subtitle, className="section-sub")]
                if subtitle
                else [icon(glyph, "me-2 text-muted"), title]
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
                        card_header(1, "cash-coin", "Income & Method", "Tell us what you earn and how you like to budget"),
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
                                                            id="income-amount", type="number", min=0, placeholder="5000"
                                                        ),
                                                        dbc.FormFeedback(
                                                            "Enter your monthly income to generate a budget.",
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
                                                    "Presets assume take-home (net) pay — no automatic tax "
                                                    "conversion is applied when Gross is selected.",
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
                                        dbc.Col(
                                            [
                                                dbc.Label("Savings Rate (%)"),
                                                dbc.InputGroup(
                                                    [
                                                        dbc.Input(
                                                            id="savings-rate-input", type="number", min=0, max=100, value=20
                                                        ),
                                                        dbc.InputGroupText("%"),
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
                                dbc.Button(
                                    [icon("magic", "me-2"), "Generate Suggested Budget"],
                                    id="generate-budget-btn",
                                    color="primary",
                                    className="mt-3",
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
                            extra=dbc.Badge("Unallocated: $0.00", id="unallocated-badge", color="success", pill=True),
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
                                            html.Div("or click to browse", className="small"),
                                        ]
                                    ),
                                    className="dropzone",
                                ),
                                dcc.Loading(html.Div(id="upload-alert", className="mt-3"), type="dot", color="#3b5bdb"),
                                html.Div(id="category-review"),
                            ]
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Card(
                    [
                        card_header(4, "bar-chart-line", "Budget vs. Actual", "Your plan against the last 30 days of spending"),
                        dbc.CardBody(dcc.Loading(html.Div(id="budget-vs-actual-section"), type="dot", color="#3b5bdb")),
                    ],
                    className="mb-4",
                ),
            ],
            className="app-shell",
        ),
    ]
)


@app.callback(
    Output("savings-rate-collapse", "is_open"),
    Output("preset-description", "children"),
    Input("preset-dropdown", "value"),
)
def update_preset_description(preset):
    return preset == "Pay Yourself First", PRESETS.get(preset, "")


@app.callback(
    Output("income-amount", "invalid"),
    Input("generate-budget-btn", "n_clicks"),
    Input("income-amount", "value"),
)
def flag_missing_income(n_clicks, income):
    return bool(n_clicks) and not income


@app.callback(
    Output("allocations-table", "data"),
    Input("generate-budget-btn", "n_clicks"),
    State("income-amount", "value"),
    State("preset-dropdown", "value"),
    State("savings-rate-input", "value"),
    prevent_initial_call=True,
)
def generate_budget(n_clicks, income, preset, savings_rate_pct):
    if not income:
        raise PreventUpdate
    savings_rate = (savings_rate_pct or 20) / 100
    df = suggested_allocations(preset, income, savings_rate=savings_rate)
    return df.to_dict("records")


@app.callback(
    Output("unallocated-badge", "children"),
    Output("unallocated-badge", "color"),
    Output("alloc-summary", "children"),
    Input("allocations-table", "data"),
    Input("income-amount", "value"),
)
def update_unallocated_badge(table_data, income):
    income = income or 0
    summary = budget_summary(income, _clean_allocations(table_data))
    unallocated = summary["unallocated"]
    color = "success" if abs(unallocated) < 1 else ("danger" if unallocated < 0 else "warning")
    meter_color = {"success": "#1baf7a", "danger": "#e03131", "warning": "#f08c00"}[color]
    used_pct = min(100.0, summary["total_budgeted"] / income * 100) if income else 0

    def stat(label, value, extra_style=None):
        return html.Div(
            [html.Div(label, className="k"), html.Div(f"{'-' if value < 0 else ''}${abs(value):,.2f}", className="v", style=extra_style)],
            className="alloc-stat",
        )

    stats = html.Div(
        [
            stat("Income", income),
            stat("Budgeted", summary["total_budgeted"]),
            stat("Unallocated", unallocated, {"color": meter_color}),
        ],
        className="alloc-stats",
    )
    meter = html.Div(
        html.Div(style={"width": f"{used_pct:.1f}%", "background": meter_color}), className="alloc-meter mb-4"
    )
    return f"Unallocated: {'-' if unallocated < 0 else ''}${abs(unallocated):,.2f}", color, [stats, meter]


@app.callback(
    Output("cleaned-transactions-store", "data"),
    Output("transactions-store", "data"),
    Output("upload-alert", "children"),
    Output("category-review", "children"),
    Output("category-baseline-store", "data"),
    Input("upload-statement", "contents"),
    prevent_initial_call=True,
)
def handle_upload(contents):
    if contents is None:
        raise PreventUpdate

    _, content_string = contents.split(",", 1)
    raw_df = pd.read_csv(io.StringIO(base64.b64decode(content_string).decode("utf-8")))
    try:
        cleaned_df = clean_statement(raw_df)
    except ValueError as exc:
        return None, None, dbc.Alert(str(exc), color="danger"), html.Div(), None

    total_count = len(cleaned_df)
    cleaned_df = filter_to_recent_window(cleaned_df)
    categorized_df = categorize_transactions(cleaned_df, overrides=load_overrides())

    message = f"Loaded {len(cleaned_df)} transactions from the last {RECENT_WINDOW_DAYS} days"
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
)
def update_budget_vs_actual(table_data, transaction_records):
    allocations_df = _clean_allocations(table_data)
    transactions_df = pd.DataFrame.from_records(transaction_records) if transaction_records else None
    result = budget_vs_actual(allocations_df, transactions_df)

    no_data_hint = (
        []
        if transactions_df is not None
        else [
            dbc.Alert(
                [icon("info-circle", "me-2"), "Upload a statement in step 3 to see your actual spending against this plan."],
                color="light",
                className="border",
            )
        ]
    )
    return [
        *no_data_hint,
        dbc.Row(
            [
                dbc.Col([build_progress_row(row) for _, row in result.iterrows()], md=6),
                dbc.Col(build_budget_chart(result), md=6),
            ]
        ),
        build_needs_wants_section(transactions_df),
        build_drilldown_accordion(result, transactions_df),
    ]


if __name__ == "__main__":
    app.run(debug=True)
