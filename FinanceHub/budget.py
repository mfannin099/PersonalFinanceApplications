"""Budget: the Budget Studio planner as a Shiny module, tracking the shared statement."""

import pandas as pd
import plotly.graph_objects as go
from shiny import module, reactive, render, req, ui

from Budget.budgeting import (
    BUDGET_CATEGORIES,
    NON_CARD_CATEGORIES,
    PRESETS,
    budget_summary,
    budget_vs_actual,
    filter_to_recent_window,
    net_income,
    suggested_allocations,
)
from Budget.export import budget_to_csv, budget_to_pdf
from FinanceHub.shared import PALETTE, StatementState, card_header, empty_state, icon, kpi, money, style_figure

BALANCED_WITHIN = 1.0  # unallocated within $1 counts as balanced, absorbing preset rounding
STATUS_CLASS = {"Under": "ok", "Near": "near", "Over": "over", "No Budget": "over"}


def _num(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


@module.ui
def budget_ui() -> ui.Tag:
    alloc_inputs = [
        ui.input_numeric(f"alloc_{i}", cat, 0, min=None, step=10, width="100%") for i, cat in enumerate(BUDGET_CATEGORIES)
    ]
    manual_inputs = [
        ui.input_numeric(f"manual_{i}", f"{cat} spent", 0, step=10, width="100%") for i, cat in enumerate(NON_CARD_CATEGORIES)
    ]
    return ui.div(
        ui.layout_columns(
            ui.card(
                card_header("cash-coin", "1 · Income & method", "Monthly, then pick a starting point"),
                ui.input_numeric("income", "Monthly income ($)", 5000, step=100, width="100%"),
                ui.input_radio_buttons("income_type", "Income type", ["Net", "Gross"], inline=True),
                ui.panel_conditional(
                    "input.income_type === 'Gross'",
                    ui.input_numeric("tax_rate", "Estimated tax rate (%)", 22, step=1, width="100%"),
                ),
                ui.output_ui("net_readout"),
                ui.input_select("method", "Budgeting method", list(PRESETS), width="100%"),
                ui.output_ui("method_help"),
                ui.panel_conditional(
                    "input.method === 'Pay Yourself First'",
                    ui.input_numeric("savings_rate", "Savings off the top (%)", 20, step=1, width="100%"),
                ),
                ui.input_action_button("generate", "Generate suggested budget", icon=icon("magic"), class_="btn-primary w-100"),
            ),
            ui.card(
                ui.card_header(
                    ui.div(
                        ui.div(icon("sliders", "me-2 text-muted"), "2 · Allocations", class_="fw-semibold"),
                        ui.div(
                            ui.download_button("dl_csv", "CSV", class_="btn-outline-secondary btn-sm"),
                            ui.download_button("dl_pdf", "PDF", class_="btn-outline-secondary btn-sm ms-1"),
                        ),
                        class_="d-flex justify-content-between align-items-center",
                    ),
                    ui.div("Edit any amount; the summary updates as you type", class_="section-sub"),
                ),
                ui.output_ui("alloc_summary"),
                ui.layout_columns(*alloc_inputs, col_widths={"xs": (6, 6), "md": (4, 4, 4), "xl": (3, 3, 3, 3)}),
                ui.output_ui("alloc_warning"),
            ),
            col_widths={"xs": 12, "xl": (4, 8)},
        ),
        ui.card(
            card_header("bar-chart-steps", "3 · Budget vs. actual", "Most recent 30 days of the statement you loaded on Home"),
            ui.accordion(
                ui.accordion_panel(
                    ui.span(icon("pencil-square", "me-2"), "Manually tracked spending (rent, savings, debt payments)"),
                    ui.div("These never appear on a card statement, so enter what you actually spent.", class_="hint"),
                    ui.layout_columns(*manual_inputs, col_widths=(4, 4, 4)),
                    value="manual",
                ),
                open=False,
                class_="mb-3",
            ),
            ui.output_ui("actual_summary"),
            ui.layout_columns(
                ui.output_ui("progress_rows"),
                _widget("bva_chart"),
                col_widths={"xs": 12, "xl": (5, 7)},
            ),
        ),
        ui.div("Budgets are session-only — nothing is saved between runs. Use CSV/PDF to keep a copy.", class_="app-footer"),
    )


def _widget(id_: str):
    from shinywidgets import output_widget

    return output_widget(id_)


@module.server
def budget_server(input, output, session, state: StatementState):
    from shinywidgets import render_widget

    # --- Income ---------------------------------------------------------------
    @reactive.calc
    def gross() -> float:
        return max(_num(input.income()), 0.0)

    @reactive.calc
    def tax_pct() -> float | None:
        return min(max(_num(input.tax_rate()), 0.0), 100.0) if input.income_type() == "Gross" else None

    @reactive.calc
    def net() -> float:
        return net_income(gross(), input.income_type(), tax_pct())

    @render.ui
    def net_readout():
        if input.income_type() != "Gross":
            return None
        return ui.div(icon("arrow-right-circle", "me-2"), f"Net monthly income: {money(net())}", class_="section-sub mb-3")

    @render.ui
    def method_help():
        return ui.div(PRESETS[input.method()], class_="hint")

    # --- Allocations ------------------------------------------------------------
    def _apply_suggestion() -> None:
        sugg = suggested_allocations(input.method(), net(), savings_rate=min(max(_num(input.savings_rate()), 0), 100) / 100)
        amounts = dict(zip(sugg["Category"], sugg["Budgeted Amount"]))
        for i, cat in enumerate(BUDGET_CATEGORIES):
            ui.update_numeric(f"alloc_{i}", value=float(amounts[cat]))

    @reactive.effect
    @reactive.event(input.generate)
    def _generate():
        with reactive.isolate():
            has_amounts = any(_num(input[f"alloc_{i}"]()) != 0 for i in range(len(BUDGET_CATEGORIES)))
        if has_amounts:
            ui.modal_show(
                ui.modal(
                    "Generating a suggestion replaces the amounts you've entered. Continue?",
                    title="Replace current allocations?",
                    footer=ui.TagList(
                        ui.modal_button("Cancel"),
                        ui.input_action_button("confirm_generate", "Replace", class_="btn-primary"),
                    ),
                    easy_close=True,
                )
            )
        else:
            _apply_suggestion()

    @reactive.effect
    @reactive.event(input.confirm_generate)
    def _confirm():
        ui.modal_remove()
        _apply_suggestion()

    @reactive.calc
    def raw_allocations() -> list[float]:
        return [_num(input[f"alloc_{i}"]()) for i in range(len(BUDGET_CATEGORIES))]

    @reactive.calc
    def allocations() -> pd.DataFrame:
        """Budgeted amounts, with negative entries counted as $0 (they're flagged separately)."""
        return pd.DataFrame({"Category": BUDGET_CATEGORIES, "Budgeted Amount": [max(v, 0.0) for v in raw_allocations()]})

    @render.ui
    def alloc_warning():
        negatives = [c for c, v in zip(BUDGET_CATEGORIES, raw_allocations()) if v < 0]
        if not negatives:
            return None
        return ui.div(icon("exclamation-triangle-fill", "me-2"), f"Negative amounts are counted as $0: {', '.join(negatives)}.", class_="alert alert-warning mt-3 mb-0")

    @render.ui
    def alloc_summary():
        s = budget_summary(net(), allocations())
        left = s["unallocated"]
        if abs(left) <= BALANCED_WITHIN:
            chip = ui.span(icon("check-circle-fill", "me-1"), "Balanced", class_="status-chip status-good")
        elif left > 0:
            chip = ui.span(f"{money(left)} unallocated", class_="status-chip status-near")
        else:
            chip = ui.span(f"{money(-left)} over income", class_="status-chip status-over")
        pct = 0 if s["total_income"] <= 0 else min(s["total_budgeted"] / s["total_income"], 1.0) * 100
        return ui.div(
            ui.div(
                ui.div(ui.span("Take-home", class_="metric-title"), ui.div(money(s["total_income"]), class_="metric-value")),
                ui.div(ui.span("Budgeted", class_="metric-title"), ui.div(money(s["total_budgeted"]), class_="metric-value")),
                ui.div(ui.span("Unallocated", class_="metric-title"), ui.div(money(left), class_="metric-value")),
                chip,
                class_="summary-row",
            ),
            ui.div(ui.div(class_=f"meter-fill {'over' if left < -BALANCED_WITHIN else ''}", style=f"width:{pct:.0f}%"), class_="meter"),
            class_="mb-3",
        )

    # --- Actuals ------------------------------------------------------------------
    @reactive.calc
    def manual_actuals() -> dict[str, float]:
        return {c: max(_num(input[f"manual_{i}"]()), 0.0) for i, c in enumerate(NON_CARD_CATEGORIES)}

    @reactive.calc
    def recent() -> pd.DataFrame | None:
        df = state.categorized()
        return None if df is None else filter_to_recent_window(df)

    @reactive.calc
    def result() -> pd.DataFrame:
        return budget_vs_actual(allocations(), recent(), manual_actuals())

    @reactive.calc
    def has_actuals() -> bool:
        return recent() is not None or any(v > 0 for v in manual_actuals().values())

    @reactive.effect
    def _publish():
        r = result()
        state.budget.set(
            {
                "spent": float(r["Actual Amount"].sum()),
                "budgeted": float(r["Budgeted Amount"].sum()),
                "over": int((r["Status"].isin(["Over", "No Budget"])).sum()),
                "savings": float(r.loc[r["Category"] == "Savings", "Budgeted Amount"].sum()),
            }
            if r["Budgeted Amount"].sum() > 0
            else None
        )

    @reactive.calc
    def shown() -> pd.DataFrame:
        r = result()
        r = r[(r["Budgeted Amount"] > 0) | (r["Actual Amount"] > 0)]
        return r.sort_values(["Budgeted Amount", "Actual Amount"], ascending=False).reset_index(drop=True)

    @render.ui
    def actual_summary():
        r = result()
        if r["Budgeted Amount"].sum() == 0 and r["Actual Amount"].sum() == 0:
            return empty_state("wallet2", "Nothing to compare yet", "Set allocations above and load a statement on Home (or enter manual spending).")
        spent, budgeted = r["Actual Amount"].sum(), r["Budgeted Amount"].sum()
        over = int(r["Status"].isin(["Over", "No Budget"]).sum())
        note = "" if recent() is not None else " · no statement loaded yet"
        return ui.div(
            kpi("Spent", money(spent), f"last 30 days{note}", "cash-stack"),
            kpi("Budgeted", money(budgeted), "per month", "wallet2"),
            kpi("Remaining", money(budgeted - spent), "under budget" if budgeted >= spent else "over budget", "piggy-bank",
                class_="signal-bullish" if budgeted >= spent else "signal-bearish"),
            kpi("Over budget", str(over), "categories", "exclamation-triangle", class_="signal-bearish" if over else "signal-bullish"),
            class_="metric-grid mb-3",
        )

    @render.ui
    def progress_rows():
        r = shown()
        if r.empty:
            return None
        rows = []
        for row in r.itertuples():
            width = 100 if row._2 == 0 else min(row._3 / row._2 * 100, 100)
            manual = row.Category in NON_CARD_CATEGORIES
            rows.append(
                ui.div(
                    ui.div(
                        ui.span(row.Category, class_="fw-semibold"),
                        ui.span(f"{money(row._3)} of {money(row._2)}", class_="section-sub"),
                        class_="d-flex justify-content-between",
                    ),
                    ui.div(ui.div(class_=f"prog-fill {STATUS_CLASS[row.Status]}", style=f"width:{width:.0f}%"), class_="prog"),
                    ui.div("Manually entered" if manual else row.Status, class_="section-sub"),
                    class_="prog-row",
                )
            )
        return ui.div(*rows)

    @render_widget
    def bva_chart():
        r = shown()
        req(not r.empty)
        r = r.iloc[::-1]
        fig = go.Figure()
        fig.add_bar(y=r["Category"], x=r["Budgeted Amount"], name="Budgeted", orientation="h", marker_color="#c8d0ea")
        fig.add_bar(y=r["Category"], x=r["Actual Amount"], name="Actual", orientation="h", marker_color=PALETTE[0])
        style_figure(fig, max(320, 34 * len(r) + 90))
        return fig.update_layout(barmode="group").update_yaxes(tickprefix="", categoryorder="array", categoryarray=list(r["Category"])).update_xaxes(tickprefix="$")

    # --- Export ---------------------------------------------------------------------
    def _export_args() -> dict:
        gross_income = gross() if input.income_type() == "Gross" else None
        return dict(income=net(), method=input.method(), result=result(), has_actuals=has_actuals(), gross_income=gross_income, tax_rate_pct=tax_pct())

    @render.download(filename="budget.csv")
    def dl_csv():
        yield budget_to_csv(**_export_args())

    @render.download(filename="budget.pdf")
    def dl_pdf():
        yield budget_to_pdf(**_export_args())
