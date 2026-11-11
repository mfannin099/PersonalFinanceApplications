"""Home: upload the statement once and fix categories once; every section reads the same data."""

from pathlib import Path

import pandas as pd
from shiny import module, reactive, render, req, ui

from Budget.statement import read_statement_csv
from CreditCard.categorize import CATEGORIES, load_overrides, save_override
from CreditCard.cleaning import clean_statement
from FinanceHub.shared import StatementState, card_header, icon, money
from FinanceHub.signals import sample_statement


@module.ui
def home_ui() -> ui.Tag:
    return ui.div(
        ui.layout_columns(
            ui.card(
                card_header("cloud-upload", "1 · Bring in a statement", "One upload feeds both Spending and Budget"),
                ui.input_file("statement", "Credit card CSV", accept=[".csv"], button_label="Choose file…", width="100%"),
                ui.div("Needs Transaction Date, Description and Amount columns (Type and Category are used when present).", class_="hint"),
                ui.div(
                    ui.input_action_button("sample", "Try sample data", icon=icon("magic"), class_="btn-outline-primary btn-sm"),
                    ui.span("Synthetic transactions — nothing personal.", class_="section-sub ms-2"),
                ),
                ui.output_ui("load_status"),
            ),
            ui.card(
                card_header("window", "Where to go next", "Every section reads the same data"),
                ui.output_ui("next_steps"),
            ),
            col_widths={"xs": (12, 12), "lg": (7, 5)},
        ),
        ui.output_ui("review"),
    )


@module.server
def home_server(input, output, session, state: StatementState):
    @reactive.effect
    @reactive.event(input.statement)
    def _on_upload():
        files = input.statement()
        if not files:
            return
        try:
            raw = read_statement_csv(Path(files[0]["datapath"]).read_bytes())
            cleaned = clean_statement(raw)
        except ValueError as exc:
            ui.notification_show(str(exc), type="error", duration=8)
            return
        if cleaned.empty:
            ui.notification_show("No purchases found in that file (only Sale rows are kept when a Type column exists).", type="warning", duration=8)
            return
        state.load(cleaned, files[0]["name"])

    @reactive.effect
    @reactive.event(input.sample)
    def _on_sample():
        state.load(clean_statement(sample_statement()), "sample data")

    @render.ui
    def load_status():
        df = state.cleaned()
        if df is None:
            return ui.div(icon("info-circle", "me-2"), "No statement loaded yet.", class_="alert alert-info mt-3 mb-0")
        return ui.div(
            icon("check-circle-fill", "me-2"),
            f"Loaded {len(df):,} purchases from {state.source()} "
            f"({df['Transaction Date'].min():%b %d, %Y} – {df['Transaction Date'].max():%b %d, %Y}).",
            class_="alert alert-success mt-3 mb-0",
        )

    @render.ui
    def next_steps():
        items = [
            ("credit-card", "Spending", "Charts, patterns and money-saving opportunities from your transactions."),
            ("wallet2", "Budget", "Plan income by category, then track the last 30 days against it."),
            ("graph-up-arrow", "Stocks", "Compare tickers, forecast trends and read company financials."),
        ]
        return ui.div(
            *[
                ui.div(ui.div(icon(g), class_="metric-icon me-3"), ui.div(ui.div(t, class_="fw-semibold"), ui.div(d, class_="section-sub")), class_="d-flex align-items-center mb-3")
                for g, t, d in items
            ]
        )

    @reactive.calc
    def merchant_categories() -> pd.DataFrame:
        df = state.categorized()
        req(df is not None)
        return (
            df.groupby("Description")
            .agg(Category=("Category", "first"), Spend=("Amount", "sum"), Purchases=("Amount", "size"))
            .sort_values("Spend", ascending=False)
            .reset_index()
        )

    @render.ui
    def review():
        df = state.categorized()
        if df is None:
            return None
        merchants = merchant_categories()
        uncategorized = int((merchants["Category"] == "Uncategorized").sum())
        rows = [
            ui.layout_columns(
                ui.div(ui.div(r.Description, class_="fw-semibold"), ui.div(f"{r.Purchases} purchase(s) · {money(r.Spend)}", class_="section-sub")),
                ui.input_select(f"cat_{i}", None, CATEGORIES, selected=r.Category, width="100%"),
                col_widths=(7, 5),
                class_="align-items-center",
            )
            for i, r in enumerate(merchants.itertuples())
        ]
        label = f"Review categories ({uncategorized} uncategorized)" if uncategorized else "Review categories"
        return ui.card(
            card_header("tags", "2 · Check the categories", "Both Spending and Budget use these; fixes are remembered next time"),
            ui.accordion(
                ui.accordion_panel(
                    ui.span(icon("tags", "me-2"), label),
                    ui.div("Change any merchant's category, then save. Only merchants you actually changed are remembered.", class_="hint mb-2"),
                    *rows,
                    ui.input_action_button("save_categories", "Save category changes", icon=icon("check2"), class_="btn-primary mt-2"),
                    value="review",
                ),
                id="review_acc",
                open="review" if uncategorized else False,
            ),
        )

    @reactive.effect
    @reactive.event(input.save_categories)
    def _save_categories():
        with reactive.isolate():
            merchants = merchant_categories()
            saved = 0
            overrides = load_overrides()
            for i, r in enumerate(merchants.itertuples()):
                chosen = input[f"cat_{i}"]()
                if chosen and chosen != r.Category and overrides.get(r.Description) != chosen:
                    save_override(r.Description, chosen)
                    saved += 1
        if saved:
            state.bump()
            ui.notification_show(f"Saved {saved} category change(s).", type="message", duration=5)
        else:
            ui.notification_show("No category changes to save.", duration=4)
