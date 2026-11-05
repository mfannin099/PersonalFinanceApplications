"""Stock Studio — Shiny for Python entry point.

Run with ``uv run shiny run Stocks/app.py``. Shiny puts this file's own
directory on ``sys.path``, so the sibling modules are imported locally
(``forecast``, not ``Stocks.forecast``) — the same convention as the
Streamlit CreditCard app. Tests import them package-qualified instead.

Prices are fetched only when "Run analysis" is clicked (it hits Yahoo
Finance); the moving-average settings and forecast horizon update live,
since they only recompute on data already loaded.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from forecast import BAND_LABEL, MA_KINDS, crossovers, ma_trend_forecast, moving_average
from fundamentals import fundamentals_table
from market_data import MAX_TICKERS, PERIODS, fetch_closes, fetch_info, parse_tickers, rebase_to_100
from shiny import App, reactive, render, req, ui
from shinywidgets import output_widget, render_widget

HERE = Path(__file__).parent
PALETTE = ["#3b5bdb", "#eb6834", "#1baf7a", "#ae3ec9", "#f08c00"]
INK, MUTED, GRID = "#1b2437", "#6b7489", "#eef0f7"
BOOTSTRAP_ICONS = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"


def icon(name: str, class_: str = "") -> ui.Tag:
    return ui.tags.i(class_=f"bi bi-{name} {class_}".strip())


def card_header(glyph: str, title: str, subtitle: str = "") -> ui.Tag:
    return ui.card_header(
        ui.div(icon(glyph, "me-2 text-muted"), title, class_="fw-semibold"),
        ui.div(subtitle, class_="section-sub") if subtitle else None,
    )


def _value_box(title: str, value: str, subtitle: str, glyph: str, **kwargs) -> ui.Tag:
    return ui.value_box(
        title, value, ui.span(subtitle, class_="vb-sub"), showcase=icon(glyph), showcase_layout="left center", **kwargs
    )


def _style_figure(fig: go.Figure, height: int = 420, money_axis: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        legend={"orientation": "h", "y": 1.02, "x": 0, "yanchor": "bottom", "traceorder": "normal"},
        font={"family": "Inter, sans-serif", "color": INK, "size": 12},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        xaxis={"gridcolor": GRID, "showspikes": False},
        yaxis={"gridcolor": GRID, "tickprefix": "$" if money_axis else "", "tickformat": ",.0f"},
    )
    return fig


app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_text("tickers", "Tickers", value="AAPL, MSFT, NVDA", placeholder="AAPL, MSFT"),
        ui.div(f"Up to {MAX_TICKERS} symbols, separated by commas.", class_="hint"),
        ui.input_select("period", "Lookback", PERIODS, selected="1y"),
        ui.panel_conditional(
            "input.period === 'custom'",
            ui.input_date_range(
                "dates", "Date range", start=date.today() - timedelta(days=730), end=date.today(), max=date.today()
            ),
        ),
        ui.input_action_button("run", "Run analysis", icon=icon("play-fill"), class_="btn-primary w-100"),
        ui.hr(),
        ui.div("Forecast settings", class_="sidebar-section"),
        ui.input_radio_buttons("ma_kind", "Moving average", {k: k for k in MA_KINDS}, selected="SMA", inline=True),
        ui.layout_columns(
            ui.input_numeric("short_window", "Short window", 20, min=2),
            ui.input_numeric("long_window", "Long window", 50, min=3),
            col_widths=(6, 6),
        ),
        ui.input_slider("horizon", "Forecast horizon (trading days)", min=5, max=90, value=30),
        ui.div("These update instantly — no need to re-run.", class_="hint"),
        width=310,
    ),
    ui.head_content(ui.tags.link(rel="stylesheet", href=BOOTSTRAP_ICONS), ui.include_css(HERE / "style.css")),
    ui.output_ui("status"),
    ui.navset_card_underline(
        ui.nav_panel(
            ui.span(icon("bar-chart-line", "me-1"), "Overview"),
            ui.card(
                card_header("graph-up", "Growth of $100", "Each ticker rebased to $100 on its first day in the period"),
                output_widget("growth_chart"),
                full_screen=True,
            ),
            ui.card(
                card_header("building", "Fundamentals", "Latest company figures from Yahoo Finance"),
                ui.output_ui("fundamentals"),
            ),
        ),
        ui.nav_panel(
            ui.span(icon("magic", "me-1"), "Forecast"),
            ui.layout_columns(ui.input_select("forecast_ticker", "Ticker", choices=[]), col_widths=(4,)),
            ui.output_ui("forecast_summary"),
            ui.card(
                card_header("activity", "Price, moving averages & forecast"),
                output_widget("forecast_chart"),
                ui.output_ui("forecast_caption"),
                full_screen=True,
            ),
            ui.card(
                card_header("signpost-split", "Moving-average crossovers", "Most recent first"),
                ui.output_ui("crossover_table"),
            ),
        ),
    ),
    ui.div(
        icon("info-circle", "me-1"),
        "For educational purposes only — not investment advice. Data from Yahoo Finance via yfinance; "
        "forecasts are simple trend projections, not predictions.",
        class_="app-footer",
    ),
    title=ui.div(ui.span(icon("graph-up-arrow"), class_="brand-mark"), "Stock Studio", class_="brand"),
    window_title="Stock Studio",
    fillable=False,
)


def server(input, output, session):
    @reactive.calc
    @reactive.event(input.run, ignore_none=False)
    def analysis() -> dict:
        tickers, rejected = parse_tickers(input.tickers())
        if rejected:
            ui.notification_show(
                f"Skipped {', '.join(rejected)} — not a ticker symbol, or over the {MAX_TICKERS}-ticker limit.",
                type="warning",
                duration=8,
            )
        if not tickers:
            return {"closes": pd.DataFrame(), "error": "Enter at least one ticker symbol, then click Run analysis."}
        try:
            if input.period() == "custom":
                start, end = input.dates()
                if start is None or end is None or start >= end:
                    return {"closes": pd.DataFrame(), "error": "Choose a custom start date before the end date."}
                closes, missing = fetch_closes(tickers, start=start, end=end + timedelta(days=1))
            else:
                closes, missing = fetch_closes(tickers, period=input.period())
        except Exception as exc:  # network/Yahoo failures surface as many exception types
            return {"closes": pd.DataFrame(), "error": f"Couldn't load prices from Yahoo Finance ({exc})."}
        if missing:
            ui.notification_show(f"No price data found for {', '.join(missing)}.", type="warning", duration=8)
        if closes.empty:
            return {"closes": closes, "error": "No price data found for those tickers in this period."}
        return {"closes": closes, "error": None}

    @reactive.effect
    def _sync_forecast_tickers():
        tickers = list(analysis()["closes"].columns)
        with reactive.isolate():
            current = input.forecast_ticker()
        ui.update_select(
            "forecast_ticker", choices=tickers, selected=current if current in tickers else (tickers or [None])[0]
        )

    @render.ui
    def status():
        error = analysis()["error"]
        if error:
            return ui.div(icon("exclamation-triangle-fill", "me-2"), error, class_="alert alert-danger")
        return None

    # --- Overview -------------------------------------------------------------

    @render_widget
    def growth_chart():
        closes = analysis()["closes"]
        req(not closes.empty)
        rebased = rebase_to_100(closes)
        fig = go.Figure()
        for i, ticker in enumerate(rebased.columns):
            series = rebased[ticker].dropna()
            change = series.iloc[-1] - 100
            fig.add_scatter(
                x=series.index,
                y=series,
                name=f"{ticker} ({change:+.1f}%)",
                line={"color": PALETTE[i % len(PALETTE)], "width": 2},
                hovertemplate=f"{ticker}: $%{{y:,.2f}}<extra></extra>",
            )
        fig.add_hline(y=100, line_dash="dot", line_color="#9aa3b8")
        return _style_figure(fig)

    @render.ui
    def fundamentals():
        closes = analysis()["closes"]
        req(not closes.empty)
        table = fundamentals_table({ticker: fetch_info(ticker) for ticker in closes.columns})
        header = ui.tags.tr(
            ui.tags.th("Metric"),
            *[
                ui.tags.th(ui.span(class_="dot", style=f"background:{PALETTE[i % len(PALETTE)]}"), ticker)
                for i, ticker in enumerate(table.columns)
            ],
        )
        rows = [ui.tags.tr(ui.tags.td(label), *[ui.tags.td(v) for v in table.loc[label]]) for label in table.index]
        return ui.div(
            ui.tags.table(ui.tags.thead(header), ui.tags.tbody(*rows), class_="table fundamentals-table"),
            class_="table-responsive",
        )

    # --- Forecast -------------------------------------------------------------

    @reactive.calc
    def forecast_result() -> dict:
        closes = analysis()["closes"]
        ticker = input.forecast_ticker()
        req(not closes.empty, ticker in closes.columns)
        short, long, horizon, kind = input.short_window(), input.long_window(), input.horizon(), input.ma_kind()
        if not short or not long or short < 2 or long <= short:
            return {"error": "The short window must be at least 2 days and smaller than the long window."}

        close = closes[ticker].dropna()
        short_ma, long_ma = moving_average(close, short, kind), moving_average(close, long, kind)
        result = {
            "ticker": ticker,
            "close": close,
            "short_ma": short_ma,
            "long_ma": long_ma,
            "crosses": crossovers(short_ma, long_ma),
            "short": short,
            "long": long,
            "kind": kind,
            "horizon": horizon,
            "forecast": None,
            "error": None,
        }
        try:
            result["forecast"] = ma_trend_forecast(close, long, horizon, kind)
        except ValueError as exc:
            result["error"] = str(exc)
        return result

    @render.ui
    def forecast_summary():
        res = forecast_result()
        if "close" not in res:
            return ui.div(icon("exclamation-triangle-fill", "me-2"), res["error"], class_="alert alert-warning")
        close, forecast, crosses = res["close"], res["forecast"], res["crosses"]
        last = close.iloc[-1]
        boxes = [
            _value_box("Last close", f"${last:,.2f}", close.index[-1].strftime("%b %d, %Y"), "cash"),
        ]
        if forecast is not None:
            end = forecast.iloc[-1]
            change = end["Forecast"] / last - 1
            boxes += [
                _value_box(
                    f"Trend in {res['horizon']} days",
                    f"${end['Forecast']:,.2f}",
                    f"{change:+.1%} from last close",
                    "graph-up-arrow" if change >= 0 else "graph-down-arrow",
                ),
                _value_box(
                    BAND_LABEL.capitalize(),
                    f"${end['Lower']:,.0f} – ${end['Upper']:,.0f}",
                    forecast.index[-1].strftime("by %b %d, %Y"),
                    "arrows-expand",
                ),
            ]
        if crosses.empty:
            signal = _value_box("Latest crossover", "None", "in this period", "dash-circle")
        else:
            latest = crosses.iloc[-1]
            signal = _value_box(
                "Latest crossover",
                latest["Signal"],
                f"since {latest['Date']:%b %d, %Y}",
                "arrow-up-right-circle" if latest["Signal"] == "Bullish" else "arrow-down-right-circle",
                class_=f"signal-{latest['Signal'].lower()}",
            )
        boxes.append(signal)
        warning = (
            ui.div(icon("info-circle", "me-2"), res["error"], class_="alert alert-warning mb-0") if res["error"] else None
        )
        return ui.TagList(ui.layout_columns(*boxes, fill=False), warning)

    @render_widget
    def forecast_chart():
        res = forecast_result()
        req("close" in res)
        close, kind = res["close"], res["kind"]
        fig = go.Figure()
        fig.add_scatter(x=close.index, y=close, name="Close", line={"color": "#8a94ad", "width": 1.5})
        fig.add_scatter(
            x=res["short_ma"].index, y=res["short_ma"], name=f"{res['short']}-day {kind}",
            line={"color": PALETTE[1], "width": 2},
        )
        fig.add_scatter(
            x=res["long_ma"].index, y=res["long_ma"], name=f"{res['long']}-day {kind}",
            line={"color": PALETTE[0], "width": 2},
        )
        for signal, color, symbol in (("Bullish", "#1baf7a", "triangle-up"), ("Bearish", "#e03131", "triangle-down")):
            hits = res["crosses"][res["crosses"]["Signal"] == signal]
            if not hits.empty:
                fig.add_scatter(
                    x=hits["Date"], y=hits["Level"], mode="markers", name=f"{signal} cross",
                    marker={"color": color, "symbol": symbol, "size": 12, "line": {"color": "white", "width": 1}},
                )

        forecast = res["forecast"]
        if forecast is not None:
            # Start the band and line at the last close so the projection visibly continues the price.
            last = close.iloc[-1]
            anchor = pd.DataFrame({"Forecast": [last], "Lower": [last], "Upper": [last]}, index=[close.index[-1]])
            path = pd.concat([anchor, forecast])
            fig.add_scatter(
                x=path.index, y=path["Upper"], line={"width": 0}, showlegend=False, hoverinfo="skip"
            )
            fig.add_scatter(
                x=path.index, y=path["Lower"], line={"width": 0}, fill="tonexty",
                fillcolor="rgba(59, 91, 219, 0.14)", name=BAND_LABEL, hoverinfo="skip",
            )
            fig.add_scatter(
                x=path.index, y=path["Forecast"], name="Trend forecast",
                line={"color": PALETTE[0], "width": 2.5, "dash": "dash"},
            )
        return _style_figure(fig, height=460)

    @render.ui
    def forecast_caption():
        res = forecast_result()
        req("close" in res and res["forecast"] is not None)
        return ui.div(
            f"The dashed line extends the slope of the {res['long']}-day {res['kind']} over its last "
            f"{res['long']} days, starting from the last close. The shaded {BAND_LABEL} widens with the "
            "stock's daily volatility over the last 60 trading days. Weekends are skipped; market holidays aren't.",
            class_="section-sub px-3 pb-3",
        )

    @render.ui
    def crossover_table():
        res = forecast_result()
        req("close" in res)
        crosses = res["crosses"].sort_values("Date", ascending=False).head(10)
        if crosses.empty:
            return ui.div(
                f"The {res['short']}- and {res['long']}-day averages didn't cross in this period.",
                class_="section-sub p-3",
            )
        rows = [
            ui.tags.tr(
                ui.tags.td(f"{row.Date:%b %d, %Y}"),
                ui.tags.td(ui.span(row.Signal, class_=f"status-chip status-{row.Signal.lower()}")),
                ui.tags.td(f"${row.Level:,.2f}", class_="text-end"),
            )
            for row in crosses.itertuples()
        ]
        head = ui.tags.tr(ui.tags.th("Date"), ui.tags.th("Signal"), ui.tags.th("At price", class_="text-end"))
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*rows), class_="table fundamentals-table mb-0")


app = App(app_ui, server)
