"""Spending: the CreditCard dashboard rebuilt as a Shiny module (Plotly charts)."""

import pandas as pd
import plotly.graph_objects as go
from shiny import module, reactive, render, req, ui
from shiny.types import SilentException

from CreditCard.analytics import (
    daily_spending_stats,
    needs_vs_wants,
    share_of_wallet,
    spend_by_period_and_category,
    spend_by_weekday,
    transactions_for_period,
)
from FinanceHub.shared import PALETTE, StatementState, card_header, empty_state, icon, kpi, money, style_figure
from FinanceHub.signals import savings_signals

FREQ = {"Daily": "D", "Weekly": "W", "Monthly": "ME"}
NOUN = {"Daily": "day", "Weekly": "week", "Monthly": "month"}
CLASS_COLORS = {"Essential": PALETTE[0], "Discretionary": PALETTE[1], "Other": PALETTE[5]}


def _table(df: pd.DataFrame, **kw) -> render.DataGrid:
    return render.DataGrid(df, width="100%", summary=False, **kw)


def dashboard() -> ui.TagList:
    """The full dashboard; only rendered once a statement is loaded."""
    return ui.TagList(
            ui.output_ui("kpis"),
            ui.card(
                card_header("people", "Venmo splits", "Select purchases you split, then record how many people shared them"),
                ui.output_data_frame("txn_table"),
                ui.layout_columns(
                    ui.input_numeric("split_count", "People sharing (incl. you)", 2, min=2, max=10, step=1),
                    ui.div(
                        ui.input_action_button("apply_split", "Mark selected as Venmoed", class_="btn-primary btn-sm"),
                        ui.input_action_button("clear_split", "Clear selected", class_="btn-outline-secondary btn-sm ms-2"),
                        class_="pt-4",
                    ),
                    col_widths=(4, 8),
                ),
                ui.div("Select rows in the table above (click; shift/ctrl-click for several).", class_="hint"),
                full_screen=True,
            ),
            ui.layout_columns(
                ui.card(card_header("bar-chart", "Spending by category"), ui.input_slider("top_n", "Items to show (all three charts)", 3, 25, 10, step=1, ticks=False), output_widget_("cat_bars")),
                ui.card(card_header("shop", "Top merchants by spend"), output_widget_("merchant_bars")),
                ui.card(card_header("repeat", "Most frequent merchants"), output_widget_("freq_bars")),
                col_widths={"xs": (12, 12, 12), "xl": (4, 4, 4)},
            ),
            ui.layout_columns(
                ui.card(card_header("pie-chart", "Share of wallet"), output_widget_("donut")),
                ui.card(
                    card_header("layout-split", "Needs vs. wants", "Essential vs. discretionary spend"),
                    output_widget_("needs_chart"),
                    ui.output_ui("needs_chips"),
                ),
                col_widths={"xs": (12, 12), "lg": (6, 6)},
            ),
            ui.card(
                card_header("calendar-week", "Spending by day of week", "Bars are dollars; the line is the average number of purchases"),
                ui.input_radio_buttons("weekday_metric", None, ["Total", "Average", "Median"], inline=True),
                output_widget_("weekday_chart"),
                ui.output_ui("weekday_caption"),
            ),
            ui.card(
                card_header("graph-up", "Spending over time"),
                ui.input_radio_buttons("agg", None, list(FREQ), inline=True),
                output_widget_("time_chart"),
                ui.output_ui("period_picker"),
                ui.output_data_frame("period_table"),
                ui.output_ui("period_caption"),
            ),
            ui.card(
                card_header("layers", "Spending by category over time"),
                ui.layout_columns(
                    ui.input_radio_buttons("cat_time_type", "Chart type", ["Stacked Area", "Lines"], inline=True),
                    ui.output_ui("cat_time_picker"),
                    col_widths=(4, 8),
                ),
                output_widget_("cat_time_chart"),
            ),
            ui.card(
                card_header("lightbulb", "Money-saving opportunities", "Four signals; merchants hit by two or more are the best places to cut"),
                ui.layout_columns(
                    ui.input_slider("months_seen", "Recurring: months seen", 1, 12, 3, step=1),
                    ui.input_slider("min_count", "Frequent: min purchases", 2, 20, 5, step=1),
                    ui.input_slider("max_avg", "Frequent: max average $", 5, 50, 25, step=5),
                    ui.input_slider("z_threshold", "One-off: z-score", 1, 5, 3, step=0.5),
                    ui.input_slider("new_days", "New: days since first seen", 15, 180, 60, step=15),
                    col_widths={"xs": 12, "md": (4, 4, 4), "xl": (2, 2, 2, 3, 3)},
                ),
                ui.output_ui("signal_tables"),
            ),
            ui.card(
                card_header("stars", "Recommendations", "Merchants flagged by at least two of the signals above"),
                ui.output_ui("recommendations"),
            ),
    )


@module.ui
def spending_ui() -> ui.Tag:
    return ui.layout_sidebar(
        ui.sidebar(
            ui.div(icon("funnel", "me-2"), "Filters", class_="sidebar-section"),
            ui.output_ui("filters"),
            width=300,
        ),
        ui.output_ui("body"),
        fillable=False,
    )


def output_widget_(id_: str):
    from shinywidgets import output_widget

    return output_widget(id_)


@module.server
def spending_server(input, output, session, state: StatementState):
    from shinywidgets import render_widget

    venmo = reactive.Value({})  # original row index -> number of people sharing

    @reactive.effect
    @reactive.event(state.cleaned)
    def _reset_venmo():
        venmo.set({})

    # --- Filters ------------------------------------------------------------
    @render.ui
    def filters():
        df = state.categorized()
        if df is None:
            return ui.div("Load a statement on Home first.", class_="hint")
        lo, hi = float(df["Amount"].min()), float(df["Amount"].max())
        merchants, categories = sorted(df["Description"].unique()), sorted(df["Category"].unique())
        return ui.TagList(
            ui.input_selectize("merchants", "Merchants", merchants, selected=merchants, multiple=True),
            ui.input_selectize("categories", "Categories", categories, selected=categories, multiple=True),
            ui.input_date_range(
                "dates", "Date range", start=df["Transaction Date"].min().date(), end=df["Transaction Date"].max().date(),
                min=df["Transaction Date"].min().date(), max=df["Transaction Date"].max().date(),
            ),
            ui.input_slider("amount", "Amount ($)", min=0, max=max(hi, lo + 1), value=(lo, hi), pre="$"),
        )

    def _value(read, default):
        """An input's value, or ``default`` while it doesn't exist yet (e.g. the sidebar is collapsed
        and its filters haven't rendered) — so the charts never wait on the filter panel."""
        try:
            value = read()
        except SilentException:
            return default
        return default if value is None else value

    @reactive.calc
    def filtered() -> pd.DataFrame:
        df = state.categorized()
        req(df is not None)
        dates = df["Transaction Date"].dt.date
        start, end = _value(input.dates, (dates.min(), dates.max()))
        lo, hi = _value(input.amount, (df["Amount"].min(), df["Amount"].max()))
        mask = (
            df["Description"].isin(_value(input.merchants, df["Description"].unique()))
            & df["Category"].isin(_value(input.categories, df["Category"].unique()))
            & (dates >= start) & (dates <= end)
            & (df["Amount"] >= lo) & (df["Amount"] <= hi)
        )
        return df[mask]

    @render.ui
    def body():
        if state.categorized() is None:
            return empty_state("inbox", "No statement loaded", "Upload a CSV or try the sample data on the Home tab.")
        return dashboard()

    # --- KPIs and Venmo -----------------------------------------------------
    def with_venmo(df: pd.DataFrame) -> pd.DataFrame:
        splits = venmo()
        out = df.copy()
        out["People"] = out.index.map(lambda i: splits.get(i, 1))
        out["Venmoed"] = out["Amount"] * (out["People"] - 1) / out["People"]
        return out

    @render.ui
    def kpis():
        df = with_venmo(filtered())
        if df.empty:
            return empty_state("funnel", "Nothing matches these filters", "Widen the filters in the sidebar.")
        stats = daily_spending_stats(df)
        return ui.div(
            kpi("Purchases", f"{len(df):,}", f"avg {money(df['Amount'].mean())}", "receipt"),
            kpi("Total spend", money(df["Amount"].sum()), f"median day {money(stats['median_daily_spend'])}", "cash-stack"),
            kpi("Venmoed purchases", f"{int((df['People'] > 1).sum())}", "split with others", "people"),
            kpi("Owed back via Venmo", money(df["Venmoed"].sum()), f"net spend {money(df['Amount'].sum() - df['Venmoed'].sum())}", "arrow-left-right"),
            class_="metric-grid mb-3",
        )

    @render.data_frame
    def txn_table():
        df = with_venmo(filtered()).sort_values("Transaction Date", ascending=False)
        view = df[["Transaction Date", "Description", "Category", "Amount", "People", "Venmoed"]].copy()
        view["Transaction Date"] = view["Transaction Date"].dt.strftime("%Y-%m-%d")
        view = view.rename(columns={"People": "Split between", "Venmoed": "Amount Venmoed"})
        view["Amount"] = view["Amount"].round(2)
        view["Amount Venmoed"] = view["Amount Venmoed"].round(2)
        return render.DataGrid(view, selection_mode="rows", height="320px", width="100%", summary=True)

    @reactive.effect
    @reactive.event(input.apply_split)
    def _apply_split():
        rows = txn_table.data_view(selected=True).index
        if len(rows) == 0:
            ui.notification_show("Select one or more purchases first.", type="warning")
            return
        n = int(input.split_count() or 2)
        venmo.set({**venmo(), **{i: n for i in rows}})

    @reactive.effect
    @reactive.event(input.clear_split)
    def _clear_split():
        rows = set(txn_table.data_view(selected=True).index)
        venmo.set({k: v for k, v in venmo().items() if k not in rows})

    # --- Overview charts ----------------------------------------------------
    @reactive.calc
    def top_n() -> int:
        try:
            return int(input.top_n())
        except Exception:
            return 10

    @render_widget
    def cat_bars():
        df = filtered()
        req(not df.empty)
        s = df.groupby("Category")["Amount"].sum().sort_values(ascending=False).head(top_n())
        fig = go.Figure(go.Bar(x=s.index, y=s.values, marker_color=PALETTE[0], hovertemplate="%{x}: $%{y:,.2f}<extra></extra>"))
        return style_figure(fig, 360).update_xaxes(tickangle=-30)

    @render_widget
    def merchant_bars():
        df = filtered()
        req(not df.empty)
        s = df.groupby("Description")["Amount"].sum().sort_values().tail(top_n())
        fig = go.Figure(go.Bar(x=s.values, y=s.index, orientation="h", marker_color=PALETTE[1], hovertemplate="%{y}: $%{x:,.2f}<extra></extra>"))
        style_figure(fig, 360)
        return fig.update_xaxes(tickprefix="$").update_yaxes(tickprefix="", gridcolor="rgba(0,0,0,0)")

    @render_widget
    def freq_bars():
        df = filtered()
        req(not df.empty)
        s = df.groupby("Description")["Amount"].size().sort_values(ascending=False).head(top_n())
        fig = go.Figure(go.Bar(x=s.index, y=s.values, marker_color=PALETTE[5], hovertemplate="%{x}: %{y} purchases<extra></extra>"))
        style_figure(fig, 360, money=False)
        return fig.update_xaxes(tickangle=-30)

    @render_widget
    def donut():
        df = filtered()
        req(not df.empty)
        w = share_of_wallet(df)
        fig = go.Figure(go.Pie(labels=w["Category"], values=w["Amount"], hole=0.55, marker={"colors": PALETTE * 3}, sort=False,
                               hovertemplate="%{label}: $%{value:,.2f} (%{percent})<extra></extra>"))
        return style_figure(fig, 360).update_layout(legend={"orientation": "v", "y": 0.5, "x": 1.02})

    @render_widget
    def needs_chart():
        df = filtered()
        req(not df.empty)
        fig = go.Figure()
        for r in needs_vs_wants(df).itertuples():
            fig.add_bar(x=[r.Amount], y=[""], orientation="h", name=r.Classification, marker_color=CLASS_COLORS[r.Classification],
                        hovertemplate=f"{r.Classification}: ${r.Amount:,.2f} ({r.pct_of_total:.1f}%)<extra></extra>")
        style_figure(fig, 110).update_layout(barmode="stack", margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
        return fig.update_xaxes(visible=False).update_yaxes(visible=False)

    @render.ui
    def needs_chips():
        df = filtered()
        req(not df.empty)
        chips = [
            ui.div(
                ui.span(class_="dot", style=f"background:{CLASS_COLORS[r.Classification]}"),
                ui.span(r.Classification, class_="fw-semibold me-1"),
                f"{money(r.Amount)} ({r.pct_of_total:.0f}%)",
                class_="chip",
            )
            for r in needs_vs_wants(df).itertuples()
        ]
        return ui.TagList(
            ui.div(*chips, class_="chip-row"),
            ui.div("Essential: bills, groceries, gas, health, automotive, fees. Discretionary: food & drink, entertainment, shopping, travel, personal. Other: transfers and uncategorized.", class_="section-sub mt-2"),
        )

    # --- Weekday ------------------------------------------------------------
    @render_widget
    def weekday_chart():
        df = filtered()
        req(not df.empty)
        w = spend_by_weekday(df)
        metric = input.weekday_metric()
        fig = go.Figure()
        fig.add_bar(x=w["Weekday"], y=w[metric], name=f"{metric} spend", marker_color=PALETTE[5], hovertemplate=f"{metric}: $%{{y:,.2f}}<extra></extra>")
        fig.add_scatter(x=w["Weekday"], y=w["Avg Transaction Count"], name="Avg purchases", yaxis="y2", mode="lines+markers", line={"color": PALETTE[1], "width": 2.5})
        style_figure(fig, 360)
        return fig.update_layout(yaxis2={"overlaying": "y", "side": "right", "showgrid": False, "title": "Avg purchases / day"}, hovermode="x unified")

    @render.ui
    def weekday_caption():
        df = filtered()
        req(not df.empty)
        s = daily_spending_stats(df)
        return ui.div(
            f"Across all days (including zero-spend ones): median daily spend {money(s['median_daily_spend'])}, "
            f"{s['avg_daily_transactions']:.2f} purchases/day on average.",
            class_="section-sub mt-2",
        )

    # --- Over time ----------------------------------------------------------
    @reactive.calc
    def time_spend() -> pd.DataFrame:
        df = filtered()
        req(not df.empty)
        return df.set_index("Transaction Date").resample(FREQ[input.agg()])["Amount"].sum().reset_index()

    @render_widget
    def time_chart():
        ts = time_spend()
        fig = go.Figure(go.Scatter(x=ts["Transaction Date"], y=ts["Amount"], mode="lines+markers", line={"color": PALETTE[0], "width": 2.5},
                                   hovertemplate="%{x|%b %d, %Y}: $%{y:,.2f}<extra></extra>"))
        return style_figure(fig, 340)

    @render.ui
    def period_picker():
        ts = time_spend()
        labels = {str(d.date()): d.strftime("%b %d, %Y") for d in ts["Transaction Date"]}
        return ui.input_select("period", f"See a {NOUN[input.agg()]}'s transactions", labels, selected=list(labels)[-1], width="320px")

    @reactive.calc
    def period_rows() -> pd.DataFrame:
        req(input.period())
        return transactions_for_period(filtered(), pd.Timestamp(input.period()), FREQ[input.agg()]).sort_values("Transaction Date")

    @render.data_frame
    def period_table():
        d = period_rows()[["Transaction Date", "Description", "Category", "Amount"]].copy()
        d["Transaction Date"] = d["Transaction Date"].dt.strftime("%Y-%m-%d")
        return _table(d, height="240px")

    @render.ui
    def period_caption():
        d = period_rows()
        return ui.div(f"{len(d)} transaction(s) totaling {money(d['Amount'].sum())}", class_="section-sub mt-2")

    @render.ui
    def cat_time_picker():
        df = state.categorized()
        req(df is not None)
        cats = sorted(df["Category"].unique())
        return ui.input_selectize("cat_time_cats", "Categories to show", cats, selected=cats, multiple=True, width="100%")

    @render_widget
    def cat_time_chart():
        df = filtered()
        chosen = input.cat_time_cats()
        req(not df.empty, chosen)
        sub = df[df["Category"].isin(chosen)]
        req(not sub.empty)
        data = spend_by_period_and_category(sub, freq=FREQ[input.agg()], top_n=len(chosen))
        fig = go.Figure()
        for i, (cat, g) in enumerate(data.groupby("Category")):
            color = PALETTE[i % len(PALETTE)]
            if input.cat_time_type() == "Stacked Area":
                fig.add_scatter(x=g["Transaction Date"], y=g["Amount"], name=cat, stackgroup="one", line={"color": color, "width": 0.5})
            else:
                fig.add_scatter(x=g["Transaction Date"], y=g["Amount"], name=cat, mode="lines+markers", line={"color": color, "width": 2})
        return style_figure(fig, 380).update_layout(hovermode="x unified")

    # --- Money saving -------------------------------------------------------
    @reactive.calc
    def signals() -> dict:
        df = filtered()
        req(not df.empty)
        return savings_signals(
            df, months_seen=input.months_seen(), min_count=input.min_count(), max_avg=float(input.max_avg()),
            z_threshold=float(input.z_threshold()), new_days=input.new_days(),
        )

    def _signal_card(title: str, glyph: str, frame: pd.DataFrame) -> ui.Tag:
        if frame.empty:
            body = ui.div("None found with these settings.", class_="section-sub")
        else:
            rows = [ui.tags.tr(ui.tags.td(r.Description), ui.tags.td(str(r._2), class_="text-end")) for r in frame.head(12).itertuples()]
            body = ui.tags.table(ui.tags.tbody(*rows), class_="table fundamentals-table mb-0")
        return ui.div(ui.div(icon(glyph, "me-2"), title, ui.span(f" · {len(frame)}", class_="section-sub"), class_="fw-semibold mb-2"), body, class_="signal-box")

    @render.ui
    def signal_tables():
        s = signals()
        return ui.layout_columns(
            _signal_card("Recurring merchants", "arrow-repeat", s["recurring"]),
            _signal_card("Frequent small purchases", "cup-hot", s["small"]),
            _signal_card("Large one-offs", "exclamation-diamond", s["one_off"]),
            _signal_card("New merchants", "stars", s["new"]),
            col_widths={"xs": 12, "md": (6, 6), "xl": (3, 3, 3, 3)},
        )

    @render.ui
    def recommendations():
        c = signals()["candidates"]
        if c.empty:
            return ui.div("No merchant is flagged by two or more signals with the current settings.", class_="section-sub")
        rows = [
            ui.tags.tr(ui.tags.td(r.Description), ui.tags.td(str(r.Transactions), class_="text-end"), ui.tags.td(money(r._3), class_="text-end"))
            for r in c.itertuples()
        ]
        head = ui.tags.tr(ui.tags.th("Merchant"), ui.tags.th("Purchases", class_="text-end"), ui.tags.th("Total", class_="text-end"))
        return ui.TagList(
            ui.div(ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows), class_="table fundamentals-table"), class_="table-responsive"),
            ui.div(icon("piggy-bank", "me-2"), f"Spend at flagged merchants: {money(c['Total Amount'].sum())} — an upper bound on what cutting them could save.", class_="section-sub mt-2"),
        )
