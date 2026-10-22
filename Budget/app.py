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
from CreditCard.categorize import CATEGORIES, categorize_transactions, load_overrides, save_override, uncategorized_merchants
from CreditCard.cleaning import clean_statement

STATUS_COLOR = {"Under": "success", "Near": "warning", "Over": "danger", "No Budget": "secondary"}

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = "Budget"


def _clean_allocations(table_data: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(table_data) if table_data else pd.DataFrame(columns=["Category", "Budgeted Amount"])
    df["Budgeted Amount"] = pd.to_numeric(df.get("Budgeted Amount"), errors="coerce").fillna(0.0)
    return df


def build_uncategorized_review(merchants: list[str]):
    if not merchants:
        return html.Div()
    rows = [
        dbc.Row(
            [
                dbc.Col(merchant, md=6),
                dbc.Col(
                    dcc.Dropdown(
                        id={"type": "merchant-cat", "index": merchant},
                        options=[{"label": c, "value": c} for c in CATEGORIES],
                        value="Uncategorized",
                        clearable=False,
                    ),
                    md=6,
                ),
            ],
            className="mb-2",
        )
        for merchant in merchants
    ]
    return dbc.Card(
        dbc.CardBody(
            [
                html.H6(f"Review {len(merchants)} uncategorized merchant(s)"),
                html.P(
                    "Assign a category — it's remembered next time this merchant shows up.",
                    className="text-muted small",
                ),
                *rows,
            ]
        ),
        className="mt-3",
    )


def build_progress_row(row: pd.Series):
    pct, actual, budgeted, status = row["Pct Used"], row["Actual Amount"], row["Budgeted Amount"], row["Status"]
    display_pct = min(pct, 100) if pct is not None else (100 if actual > 0 else 0)
    caption = (
        html.Div("Manually tracked — not on your card statement", className="text-muted small")
        if row["Category"] in NON_CARD_CATEGORIES
        else None
    )
    return html.Div(
        [
            html.Div(
                [
                    html.Span(row["Category"], className="fw-semibold"),
                    html.Span(f"${actual:,.0f} of ${budgeted:,.0f}", className="float-end small"),
                ]
            ),
            dbc.Progress(value=display_pct, color=STATUS_COLOR.get(status, "secondary"), className="mb-1"),
            caption,
        ],
        className="mb-3",
    )


def build_budget_chart(result: pd.DataFrame):
    fig = go.Figure()
    fig.add_bar(name="Budgeted", y=result["Category"], x=result["Budgeted Amount"], orientation="h")
    fig.add_bar(name="Actual", y=result["Category"], x=result["Actual Amount"], orientation="h")
    fig.update_layout(
        barmode="group",
        height=520,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        legend={"orientation": "h"},
        yaxis={"categoryorder": "total ascending"},
    )
    return dcc.Graph(figure=fig)


def build_needs_wants_section(transactions_df: pd.DataFrame | None):
    if transactions_df is None or transactions_df.empty:
        return html.Div()
    summary = needs_vs_wants(transactions_df)
    fig = go.Figure(go.Pie(labels=summary["Classification"], values=summary["Amount"], hole=0.5))
    fig.update_layout(title="Actual Spend: Needs vs. Wants vs. Other", margin={"l": 10, "r": 10, "t": 40, "b": 10})
    return dbc.Row(dbc.Col(dcc.Graph(figure=fig), md=6), className="mt-4")


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
            columns=[{"name": c, "id": c} for c in subset.columns],
            page_size=10,
            style_table={"overflowX": "auto"},
        )
        items.append(dbc.AccordionItem(table, title=f"{category} — {len(subset)} transaction(s)"))
    if not items:
        return html.Div()
    return dbc.Accordion(items, start_collapsed=True, className="mt-4")


app.layout = dbc.Container(
    [
        html.H2("📅 Budget", className="my-3"),
        dcc.Store(id="cleaned-transactions-store"),
        dcc.Store(id="transactions-store"),
        dbc.Card(
            dbc.CardBody(
                [
                    html.H5("Income & Method"),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Monthly Income"),
                                    dbc.Input(id="income-amount", type="number", min=0, placeholder="e.g. 5000"),
                                ],
                                md=4,
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Income Type"),
                                    dbc.RadioItems(
                                        id="income-type",
                                        options=[{"label": "Gross", "value": "Gross"}, {"label": "Net", "value": "Net"}],
                                        value="Net",
                                        inline=True,
                                    ),
                                    html.Div(
                                        "Presets assume take-home (net) pay — no automatic tax conversion "
                                        "is applied when Gross is selected.",
                                        className="text-muted small",
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
                                    html.Div(id="preset-description", className="text-muted small mt-1"),
                                ],
                                md=4,
                            ),
                        ]
                    ),
                    dbc.Collapse(
                        dbc.Row(
                            dbc.Col(
                                [
                                    dbc.Label("Savings Rate (%)"),
                                    dbc.Input(id="savings-rate-input", type="number", min=0, max=100, value=20),
                                ],
                                md=4,
                            ),
                            className="mt-2",
                        ),
                        id="savings-rate-collapse",
                        is_open=False,
                    ),
                    dbc.Button("Generate Suggested Budget", id="generate-budget-btn", color="primary", className="mt-3"),
                ]
            ),
            className="mb-4",
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.H5("Allocations", className="d-inline-block me-3"),
                            dbc.Badge("Unallocated: $0.00", id="unallocated-badge", color="success"),
                        ]
                    ),
                    dash_table.DataTable(
                        id="allocations-table",
                        data=[{"Category": c, "Budgeted Amount": 0.0} for c in BUDGET_CATEGORIES],
                        columns=[
                            {"name": "Category", "id": "Category", "editable": False},
                            {"name": "Budgeted Amount", "id": "Budgeted Amount", "editable": True, "type": "numeric"},
                        ],
                        editable=True,
                        style_table={"overflowX": "auto"},
                        page_size=20,
                    ),
                ]
            ),
            className="mb-4",
        ),
        dbc.Card(
            dbc.CardBody(
                [
                    html.H5("Upload & Categorize Credit Card Statement"),
                    html.P(
                        "Expected CSV columns: Transaction Date, Description, Amount "
                        "(Type and Category are used when present).",
                        className="text-muted small",
                    ),
                    dcc.Upload(
                        id="upload-statement",
                        children=html.Div("Drag and drop or click to upload a statement CSV"),
                        className="border rounded p-4 text-center",
                    ),
                    html.Div(id="upload-alert", className="mt-2"),
                    html.Div(id="uncategorized-review"),
                ]
            ),
            className="mb-4",
        ),
        dbc.Card(
            dbc.CardBody([html.H5("Budget vs. Actual"), html.Div(id="budget-vs-actual-section")]),
            className="mb-4",
        ),
    ],
    fluid=True,
    className="py-4",
)


@app.callback(
    Output("savings-rate-collapse", "is_open"),
    Output("preset-description", "children"),
    Input("preset-dropdown", "value"),
)
def update_preset_description(preset):
    return preset == "Pay Yourself First", PRESETS.get(preset, "")


@app.callback(
    Output("allocations-table", "data"),
    Input("generate-budget-btn", "n_clicks"),
    State("income-amount", "value"),
    State("preset-dropdown", "value"),
    State("savings-rate-input", "value"),
    prevent_initial_call=True,
)
def generate_budget(n_clicks, income, preset, savings_rate_pct):
    income = income or 0
    savings_rate = (savings_rate_pct or 20) / 100
    df = suggested_allocations(preset, income, savings_rate=savings_rate)
    return df.to_dict("records")


@app.callback(
    Output("unallocated-badge", "children"),
    Output("unallocated-badge", "color"),
    Input("allocations-table", "data"),
    State("income-amount", "value"),
)
def update_unallocated_badge(table_data, income):
    income = income or 0
    unallocated = budget_summary(income, _clean_allocations(table_data))["unallocated"]
    color = "success" if abs(unallocated) < 0.01 else ("danger" if unallocated < 0 else "warning")
    return f"Unallocated: ${unallocated:,.2f}", color


@app.callback(
    Output("cleaned-transactions-store", "data"),
    Output("transactions-store", "data"),
    Output("upload-alert", "children"),
    Output("uncategorized-review", "children"),
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
        return None, None, dbc.Alert(str(exc), color="danger"), html.Div()

    total_count = len(cleaned_df)
    cleaned_df = filter_to_recent_window(cleaned_df)
    categorized_df = categorize_transactions(cleaned_df, overrides=load_overrides())

    message = f"Loaded {len(cleaned_df)} transactions from the last {RECENT_WINDOW_DAYS} days"
    message += f" (filtered from {total_count} total)." if len(cleaned_df) != total_count else "."
    alert = dbc.Alert(message, color="success")
    review = build_uncategorized_review(uncategorized_merchants(categorized_df))
    return cleaned_df.to_dict("records"), categorized_df.to_dict("records"), alert, review


@app.callback(
    Output("transactions-store", "data", allow_duplicate=True),
    Output("uncategorized-review", "children", allow_duplicate=True),
    Input({"type": "merchant-cat", "index": ALL}, "value"),
    State("cleaned-transactions-store", "data"),
    prevent_initial_call=True,
)
def resolve_uncategorized(selected_categories, cleaned_records):
    if not cleaned_records:
        raise PreventUpdate

    merchant_ids = [item["id"]["index"] for item in callback_context.inputs_list[0]]
    for merchant, chosen in zip(merchant_ids, selected_categories):
        if chosen and chosen != "Uncategorized":
            save_override(merchant, chosen)

    cleaned_df = pd.DataFrame.from_records(cleaned_records)
    categorized_df = categorize_transactions(cleaned_df, overrides=load_overrides())
    review = build_uncategorized_review(uncategorized_merchants(categorized_df))
    return categorized_df.to_dict("records"), review


@app.callback(
    Output("budget-vs-actual-section", "children"),
    Input("allocations-table", "data"),
    Input("transactions-store", "data"),
)
def update_budget_vs_actual(table_data, transaction_records):
    allocations_df = _clean_allocations(table_data)
    transactions_df = pd.DataFrame.from_records(transaction_records) if transaction_records else None
    result = budget_vs_actual(allocations_df, transactions_df)

    return [
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
