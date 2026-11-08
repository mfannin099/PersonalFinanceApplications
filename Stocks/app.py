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
from financials import GLOSSARY, GUIDE_CAVEATS, READING_ORDER, SECTION_INTRO, SECTIONS, annual_table
from financials import investor_metrics as investor_metrics_fn
from fundamentals import fundamentals_table
from ml_forecast import FEATURE_GROUPS, FOLDS, PARAM_BOUNDS, PARAMS, TUNE_GRID, train_and_forecast
from market_data import (
    MAX_TICKERS,
    PERIODS,
    fetch_closes,
    fetch_info,
    fetch_statements,
    parse_tickers,
    START_VALUE,
    rebase,
)
from shiny import App, reactive, render, req, ui
from shinywidgets import output_widget, render_widget

HERE = Path(__file__).parent
PALETTE = ["#3b5bdb", "#eb6834", "#1baf7a", "#ae3ec9", "#f08c00"]
SECTION_ICONS = {
    "Income statement": "receipt",
    "Growth": "graph-up-arrow",
    "Profitability": "percent",
    "Valuation": "tag",
    "Financial health": "shield-check",
    "Cash flow": "cash-stack",
    "Shareholder return": "piggy-bank",
    "Analyst view": "megaphone",
}
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


def ml_explainer() -> ui.Tag:
    params = ", ".join(f"{k}={v}" for k, v in PARAMS.items() if k in ("n_estimators", "max_depth", "learning_rate", "subsample", "min_child_weight", "reg_lambda"))
    groups = [ui.tags.li(ui.tags.b(f"{name}: "), ", ".join(cols)) for name, cols in FEATURE_GROUPS.items()]
    return ui.div(
        ui.tags.ol(
            ui.tags.li(
                ui.tags.b("What it predicts. "),
                "The stock's return over the 'Project ahead' horizon — not the price. Prices trend upward over time and "
                "tree models can't extrapolate past values they've seen, so a price model would just copy the recent past. "
                "Returns are far more stable, which suits trees. The price shown is the predicted return applied to the last close.",
            ),
            ui.tags.li(
                ui.tags.b("Time-based features. "),
                "For each day, only information known at that day's close:",
                ui.tags.ul(*groups),
            ),
            ui.tags.li(
                ui.tags.b("A deliberately small model. "),
                f"XGBoost builds many tiny trees, each correcting the last. Trees are only {PARAMS['max_depth']} levels deep "
                "(each can combine at most two features), learn slowly, see random subsets of rows and features, and need "
                "real support to make a split. Settings are fixed — no parameter search — because searching on a noisy "
                f"target mostly finds settings that fit the past by luck. ({params})",
            ),
            ui.tags.li(
                ui.tags.b("Walk-forward validation. "),
                f"The model is trained on the early part of history and tested on the next unseen block, {FOLDS} times, "
                "each time with more training data. A gap of one horizon between train and test stops overlapping "
                "multi-day returns from leaking the answer. This mimics how you'd actually use it.",
            ),
            ui.tags.li(
                ui.tags.b("Scored against naive guesses. "),
                "'Skill' compares the model's average error to simply guessing the typical past return; it must be "
                "above 0 to add anything. The hit rate is compared with always guessing 'up'. Correlation checks that "
                "bigger predictions go with bigger outcomes.",
            ),
            ui.tags.li(
                ui.tags.b("Final forecast and band. "),
                "A last model is fit on all days with a known outcome and applied to today's features. The shaded range "
                "is the 10th–90th percentile of the model's out-of-sample errors, i.e. how wrong it has really been.",
            ),
        ),
        ui.div(
            "Daily stock returns are very close to noise, so it's normal — and informative — for this model to show "
            "little or no skill. For prices, a random walk is famously hard to beat; ARIMA, Prophet or LSTMs face the "
            "same limit. Use the scorecard, not the point forecast. Not investment advice.",
            class_="alert alert-warning mb-0",
        ),
        class_="ml-explainer px-3 pb-3",
    )


def ml_settings() -> ui.Tag:
    """Collapsible tuning panel: feature selection, hyper-parameters and auto-tune."""
    lo, hi = PARAM_BOUNDS, PARAM_BOUNDS

    def slider(id_, label, key, step, help_):
        return ui.div(
            ui.input_slider(id_, label, min=lo[key][0], max=hi[key][1], value=PARAMS[key], step=step),
            ui.div(help_, class_="hint"),
        )

    features = [
        ui.input_checkbox_group(f"ml_feat_{i}", name, choices=cols, selected=cols, inline=True)
        for i, (name, cols) in enumerate(FEATURE_GROUPS.items())
    ]
    return ui.accordion(
        ui.accordion_panel(
            ui.span(icon("sliders", "me-2"), "Model settings (optional)"),
            ui.layout_columns(
                ui.div(
                    ui.div("Features", class_="sidebar-section"),
                    ui.div("Untick anything you don't want the model to see.", class_="hint"),
                    *features,
                ),
                ui.div(
                    ui.div("Hyper-parameters", class_="sidebar-section"),
                    slider("ml_depth", "Tree depth", "max_depth", 1, "Deeper trees capture more feature interactions but memorise noise faster."),
                    slider("ml_trees", "Number of trees", "n_estimators", 10, "More trees fit the training data harder."),
                    slider("ml_lr", "Learning rate", "learning_rate", 0.01, "How much each tree corrects the last. Lower is gentler and usually safer."),
                    slider("ml_minchild", "Min. leaf weight", "min_child_weight", 1, "Higher forces each split to rest on more days."),
                ),
                ui.div(
                    ui.div("Regularisation", class_="sidebar-section"),
                    slider("ml_subsample", "Row sampling", "subsample", 0.05, "Share of days each tree sees. Below 1 adds useful randomness."),
                    slider("ml_colsample", "Feature sampling", "colsample_bytree", 0.05, "Share of features each tree sees."),
                    slider("ml_lambda", "L2 penalty", "reg_lambda", 0.5, "Shrinks leaf values toward zero; higher is more conservative."),
                    ui.input_switch("ml_autotune", "Auto-tune depth & learning rate", value=False),
                    ui.div(
                        f"Tries {len(TUNE_GRID)} combinations (depth 1–3 × learning rate 0.02/0.05) using only data before each "
                        "test block, so the scorecard stays honest. Overrides the two sliders above. Slower.",
                        class_="hint",
                    ),
                    ui.input_action_button("ml_reset", "Reset to defaults", class_="btn-outline-secondary btn-sm"),
                ),
                col_widths=(4, 4, 4),
            ),
            ui.div(
                icon("exclamation-triangle", "me-1"),
                "More flexible settings (deeper, more trees, higher learning rate) fit the past better — and usually "
                "forecast the future worse. Judge changes by the out-of-sample skill, not the in-sample fit.",
                class_="window-warning mb-0",
            ),
            value="settings",
        ),
        id="ml_settings_acc",
        open=False,
        class_="mb-3",
    )


def financials_guide() -> ui.Tag:
    """Collapsible reading guide: where to start, then a glossary per section."""
    start = ui.accordion_panel(
        ui.span(icon("compass", "me-2"), "Start here: how to read this tab"),
        ui.div(
            "Think of this tab as a health check on each company. Work through the sections in this order:",
            class_="mb-2",
        ),
        ui.tags.ol(*[ui.tags.li(ui.tags.b(f"{name}. "), text) for name, text in READING_ORDER]),
        ui.div("Keep in mind", class_="sidebar-section mt-3"),
        ui.tags.ul(*[ui.tags.li(text) for text in GUIDE_CAVEATS]),
        ui.div(
            "Tip: hover any metric name in the tables below for a one-line definition, or open the glossary panels here "
            "for what it means and how to read it.",
            class_="section-sub",
        ),
        value="start",
    )
    panels = []
    for title, rows in SECTIONS:
        head = ui.tags.tr(ui.tags.th("Metric"), ui.tags.th("What it is"), ui.tags.th("How to read it"))
        body = [
            ui.tags.tr(
                ui.tags.td(label, class_="fw-semibold"),
                ui.tags.td(GLOSSARY[label][0]),
                ui.tags.td(GLOSSARY[label][1]),
            )
            for label, *_ in rows
        ]
        panels.append(
            ui.accordion_panel(
                ui.span(icon(SECTION_ICONS.get(title, "list-ul"), "me-2"), f"Glossary: {title}"),
                ui.div(SECTION_INTRO[title], class_="section-sub mb-2"),
                ui.div(
                    ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*body), class_="table fundamentals-table glossary-table"),
                    class_="table-responsive",
                ),
                value=f"glossary-{title}",
            )
        )
    return ui.accordion(start, *panels, id="fin_guide", open="start", multiple=False, class_="mb-3")


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
        ui.div("Two moving averages of the closing price drive the signals and the forecast below.", class_="hint"),
        ui.input_radio_buttons("ma_kind", "Average type", {k: k for k in MA_KINDS}, selected="SMA", inline=True),
        ui.div(
            "SMA weighs every day equally. EMA weighs recent days more, so it reacts faster.", class_="hint"
        ),
        ui.layout_columns(
            ui.input_numeric("short_window", "Fast average (days)", 20, min=2),
            ui.input_numeric("long_window", "Slow average (days)", 50, min=3),
            col_widths=(6, 6),
        ),
        ui.div(
            "Fast follows recent price moves; slow shows the longer trend and sets the forecast's slope. "
            "A bullish signal is when fast crosses above slow.",
            class_="hint",
        ),
        ui.div(
            ui.input_action_button("preset_20_50", "20 / 50", class_="btn-outline-secondary btn-sm"),
            ui.input_action_button("preset_50_200", "50 / 200", class_="btn-outline-secondary btn-sm"),
            ui.input_action_button("reset_forecast", "Reset", class_="btn-outline-secondary btn-sm"),
            class_="preset-row",
        ),
        ui.output_ui("window_warning"),
        ui.input_slider("horizon", "Project ahead (trading days)", min=5, max=90, value=30),
        ui.div("How far to extend the trend line. Settings update instantly — no need to re-run.", class_="hint"),
        width=310,
    ),
    ui.head_content(ui.tags.link(rel="stylesheet", href=BOOTSTRAP_ICONS), ui.include_css(HERE / "style.css")),
    ui.output_ui("status"),
    ui.navset_card_underline(
        ui.nav_panel(
            ui.span(icon("bar-chart-line", "me-1"), "Overview"),
            ui.card(
                card_header(
                    "graph-up",
                    f"Growth of ${START_VALUE:,}",
                    f"What ${START_VALUE:,} invested in each ticker on the first day of the period would be worth since — "
                    "price change plus reinvested dividends, before taxes and fees",
                ),
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
            ui.tags.details(
                ui.tags.summary(icon("question-circle", "me-1"), "How the forecast works"),
                ui.tags.ul(
                    ui.tags.li(
                        ui.tags.b("Crossovers: "),
                        "when the fast average rises above the slow one, momentum is turning up (bullish); "
                        "when it falls below, momentum is turning down (bearish).",
                    ),
                    ui.tags.li(
                        ui.tags.b("Dashed line: "),
                        "continues the slow average's recent slope, starting from the last close.",
                    ),
                    ui.tags.li(
                        ui.tags.b("Shaded band: "),
                        "where the price landed 80% of the time under the stock's recent volatility; "
                        "it widens the further out you look.",
                    ),
                    ui.tags.li(
                        ui.tags.b("Limits: "),
                        "this is a simple trend extension, not a prediction. It can't anticipate earnings, "
                        "news or market shocks.",
                    ),
                ),
                class_="explainer",
            ),
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
        ui.nav_panel(
            ui.span(icon("cpu", "me-1"), "ML Forecast"),
            ui.layout_columns(
                ui.input_select("ml_ticker", "Ticker", choices=[]),
                ui.div(
                    ui.input_action_button(
                        "train_ml", "Train XGBoost model", icon=icon("lightning-charge-fill"), class_="btn-primary"
                    ),
                    class_="ml-button",
                ),
                col_widths=(4, 4),
            ),
            ml_settings(),
            ui.output_ui("ml_status"),
            ui.output_ui("ml_summary"),
            ui.card(
                card_header("clipboard-data", "Honest scorecard", "Out-of-sample walk-forward results"),
                ui.output_ui("ml_scorecard"),
                output_widget("ml_oos_chart"),
            ),
            ui.card(card_header("bar-chart-steps", "What the model looked at"), output_widget("ml_importance_chart")),
            ui.card(
                card_header("book", "How this works", "The whole process, step by step"),
                ml_explainer(),
            ),
        ),
        ui.nav_panel(
            ui.span(icon("receipt", "me-1"), "Financials"),
            financials_guide(),
            ui.layout_columns(ui.input_select("fin_ticker", "Chart ticker", choices=[]), col_widths=(4,)),
            ui.card(
                card_header(
                    "bar-chart",
                    "Revenue, net income & free cash flow",
                    "Annual. Revenue = total sales; net income = profit after all costs; free cash flow = cash left "
                    "after capital spending. Healthy companies show all three rising together.",
                ),
                output_widget("financials_chart"),
                ui.output_ui("financials_note"),
                full_screen=True,
            ),
            ui.output_ui("investor_metrics"),
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
        with reactive.isolate():
            current_fin = input.fin_ticker()
        ui.update_select(
            "forecast_ticker", choices=tickers, selected=current if current in tickers else (tickers or [None])[0]
        )
        ui.update_select(
            "fin_ticker", choices=tickers, selected=current_fin if current_fin in tickers else (tickers or [None])[0]
        )
        with reactive.isolate():
            current_ml = input.ml_ticker()
        ui.update_select(
            "ml_ticker", choices=tickers, selected=current_ml if current_ml in tickers else (tickers or [None])[0]
        )

    @reactive.effect
    @reactive.event(input.preset_20_50)
    def _preset_20_50():
        ui.update_numeric("short_window", value=20)
        ui.update_numeric("long_window", value=50)

    @reactive.effect
    @reactive.event(input.preset_50_200)
    def _preset_50_200():
        ui.update_numeric("short_window", value=50)
        ui.update_numeric("long_window", value=200)

    @reactive.effect
    @reactive.event(input.reset_forecast)
    def _reset_forecast():
        ui.update_numeric("short_window", value=20)
        ui.update_numeric("long_window", value=50)
        ui.update_radio_buttons("ma_kind", selected="SMA")
        ui.update_slider("horizon", value=30)

    @render.ui
    def window_warning():
        closes = analysis()["closes"]
        long = input.long_window()
        if closes.empty or not long:
            return None
        history = {t: int(closes[t].notna().sum()) for t in closes.columns}
        short_ticker = min(history, key=history.get)
        needed = 2 * long - 1  # the forecast fits the slow average's last `long` points, which need `long` days to start
        if history[short_ticker] < needed:
            return ui.div(
                icon("exclamation-triangle", "me-1"),
                f"{short_ticker} has {history[short_ticker]} days of history; a {long}-day slow average needs about "
                f"{needed} days to draw the forecast (and {long + 1} for crossovers). Pick a longer lookback "
                "or a shorter window.",
                class_="window-warning",
            )
        return None

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
        rebased = rebase(closes)
        fig = go.Figure()
        for i, ticker in enumerate(rebased.columns):
            series = rebased[ticker].dropna()
            change = series.iloc[-1] / START_VALUE - 1
            fig.add_scatter(
                x=series.index,
                y=series,
                name=f"{ticker} ({change:+.1%})",
                line={"color": PALETTE[i % len(PALETTE)], "width": 2},
                hovertemplate=f"{ticker}: $%{{y:,.2f}}<extra></extra>",
            )
        fig.add_hline(y=START_VALUE, line_dash="dot", line_color="#9aa3b8")
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

    # --- ML forecast ----------------------------------------------------------

    @reactive.calc
    @reactive.event(input.train_ml)
    def ml_result() -> dict:
        """Runs only when the button is clicked."""
        closes = analysis()["closes"]
        ticker = input.ml_ticker()
        if closes.empty or ticker not in closes.columns:
            return {"error": "Load some tickers first (Run analysis), then train the model."}
        with ui.Progress(min=0, max=1) as progress:
            progress.set(0.3, message=f"Training XGBoost on {ticker}…", detail="Walk-forward validation, then a final fit")
            try:
                selected = [f for i in range(len(FEATURE_GROUPS)) for f in input[f"ml_feat_{i}"]()]
                overrides = {
                    "max_depth": input.ml_depth(),
                    "n_estimators": input.ml_trees(),
                    "learning_rate": input.ml_lr(),
                    "min_child_weight": input.ml_minchild(),
                    "subsample": input.ml_subsample(),
                    "colsample_bytree": input.ml_colsample(),
                    "reg_lambda": input.ml_lambda(),
                }
                forecast = train_and_forecast(
                    closes[ticker], input.horizon(), overrides, selected, auto_tune=input.ml_autotune()
                )
                return {"ticker": ticker, "error": None, **forecast}
            except ValueError as exc:
                return {"error": str(exc)}

    @reactive.effect
    @reactive.event(input.ml_reset)
    def _ml_reset():
        for i, cols in enumerate(FEATURE_GROUPS.values()):
            ui.update_checkbox_group(f"ml_feat_{i}", selected=cols)
        ui.update_slider("ml_depth", value=PARAMS["max_depth"])
        ui.update_slider("ml_trees", value=PARAMS["n_estimators"])
        ui.update_slider("ml_lr", value=PARAMS["learning_rate"])
        ui.update_slider("ml_minchild", value=PARAMS["min_child_weight"])
        ui.update_slider("ml_subsample", value=PARAMS["subsample"])
        ui.update_slider("ml_colsample", value=PARAMS["colsample_bytree"])
        ui.update_slider("ml_lambda", value=PARAMS["reg_lambda"])
        ui.update_switch("ml_autotune", value=False)

    @render.ui
    def ml_status():
        if input.train_ml() == 0:
            return ui.div(
                icon("lightning-charge", "me-2"),
                "Pick a ticker and click Train XGBoost model. Use a 2-year or longer lookback — the model needs "
                "a few hundred days of history. Training takes a few seconds.",
                class_="alert alert-info",
            )
        res = ml_result()
        if res.get("error"):
            return ui.div(icon("exclamation-triangle-fill", "me-2"), res["error"], class_="alert alert-warning")
        return None

    @render.ui
    def ml_summary():
        res = ml_result()
        req(not res.get("error"))
        m = res["metrics"]
        change = res["predicted_return"]
        p = res["params"]
        return ui.TagList(
            ui.layout_columns(
                _value_box(
                    f"Predicted {res['horizon']}-day return",
                    f"{change:+.1%}",
                    f"{res['ticker']} by {res['target_date']:%b %d, %Y}",
                    "graph-up-arrow" if change >= 0 else "graph-down-arrow",
                ),
                _value_box(
                    "Implied price",
                    f"${res['predicted_price']:,.2f}",
                    f"80% range ${res['lower_price']:,.0f} – ${res['upper_price']:,.0f}",
                    "cash",
                ),
                _value_box(
                    "Skill vs. naive guess",
                    f"{m['skill']:+.1%}",
                    "above 0 = beats guessing the average",
                    "bullseye",
                    class_="signal-bullish" if m["skill"] > 0 else "signal-bearish",
                ),
                _value_box(
                    "Direction hit rate",
                    f"{m['hit_rate']:.0%}",
                    f"always-'up' would score {m['up_rate']:.0%}",
                    "compass",
                ),
                fill=False,
            ),
            ui.div(icon("info-circle", "me-2"), res["verdict"], class_="alert alert-secondary"),
            ui.div(
                ui.tags.b("Settings used: "),
                f"depth {p['max_depth']}, {p['n_estimators']} trees, learning rate {p['learning_rate']:g}, "
                f"min leaf weight {p['min_child_weight']}, row sampling {p['subsample']:g}, feature sampling "
                f"{p['colsample_bytree']:g}, L2 {p['reg_lambda']:g}"
                + (" (depth and learning rate auto-tuned)" if res["auto_tuned"] else "")
                + f" · {len(res['features'])} of {len([f for cols in FEATURE_GROUPS.values() for f in cols])} features.",
                class_="section-sub mb-3",
            ),
        )

    @render.ui
    def ml_scorecard():
        res = ml_result()
        req(not res.get("error"))
        m = res["metrics"]
        rows = [
            ("Test days (never seen in training)", f"{m['test_days']:,}"),
            ("Model avg. error (MAE)", f"{m['mae_model']:.2%}"),
            ("Naive-guess avg. error", f"{m['mae_baseline']:.2%}"),
            ("Correlation, predicted vs. actual", f"{m['correlation']:+.2f}"),
            ("In-sample error (days the model trained on)", f"{res['train_mae']:.2%}"),
            ("Training rows (final model)", f"{res['n_rows']:,}"),
        ]
        body = [ui.tags.tr(ui.tags.td(k), ui.tags.td(v, class_="text-end")) for k, v in rows]
        return ui.tags.table(ui.tags.tbody(*body), class_="table fundamentals-table")

    @render_widget
    def ml_oos_chart():
        res = ml_result()
        req(not res.get("error"))
        oos = res["oos"]
        fig = go.Figure()
        fig.add_scatter(x=oos.index, y=oos["Actual"], name="Actual", line={"color": "#8a94ad", "width": 1.5})
        fig.add_scatter(x=oos.index, y=oos["Predicted"], name="Model prediction", line={"color": PALETTE[0], "width": 2})
        _style_figure(fig, height=320)
        fig.update_yaxes(tickprefix="", tickformat=".0%", title=f"{res['horizon']}-day forward return")
        return fig

    @render_widget
    def ml_importance_chart():
        res = ml_result()
        req(not res.get("error"))
        imp = res["importance"].sort_values()
        fig = go.Figure(go.Bar(x=imp.values, y=imp.index, orientation="h", marker_color=PALETTE[0]))
        _style_figure(fig, height=380)
        fig.update_layout(hovermode="closest")
        fig.update_xaxes(tickformat=".0%", title="Share of the model's total gain")
        fig.update_yaxes(tickprefix="", gridcolor="rgba(0,0,0,0)")
        return fig

    # --- Financials -----------------------------------------------------------

    @reactive.calc
    def statements() -> dict:
        """Per-ticker Yahoo info and annual statement tables for every loaded ticker."""
        closes = analysis()["closes"]
        req(not closes.empty)
        infos, annuals = {}, {}
        for ticker in closes.columns:
            infos[ticker] = fetch_info(ticker)
            stmts = fetch_statements(ticker)
            annuals[ticker] = annual_table(stmts["income"], stmts["balance"], stmts["cashflow"])
        return {"infos": infos, "annuals": annuals}

    @render_widget
    def financials_chart():
        data = statements()
        ticker = input.fin_ticker()
        req(ticker in data["annuals"])
        annual = data["annuals"][ticker]
        fig = go.Figure()
        if annual.empty:
            fig.add_annotation(text="No statement data from Yahoo for this ticker.", showarrow=False)
            return _style_figure(fig, height=360)
        annual = annual.dropna(subset=["Revenue"])  # drop years with only partial line items
        years = [f"FY{d.year}" for d in annual.index]
        for col, color in (("Revenue", PALETTE[0]), ("Net income", PALETTE[2]), ("Free cash flow", PALETTE[1])):
            fig.add_bar(
                x=years, y=annual[col] / 1e9, name=col, marker_color=color,
                hovertemplate=f"{col}: $%{{y:,.1f}}B<extra></extra>",
            )
        _style_figure(fig, height=360)
        fig.update_layout(barmode="group")
        fig.update_yaxes(ticksuffix="B")
        return fig

    @render.ui
    def financials_note():
        data = statements()
        ticker = input.fin_ticker()
        req(ticker in data["annuals"])
        annual = data["annuals"][ticker]
        req(not annual.empty)
        margins = annual[["Gross margin", "Operating margin", "Net margin"]].iloc[-1]
        parts = [f"{name.split()[0]} {value:.1%}" for name, value in margins.items() if pd.notna(value)]
        text = f"Latest fiscal year (FY{annual.index[-1].year}) margins: " + ", ".join(parts) + "." if parts else ""
        return ui.div(text, class_="section-sub px-3 pb-3") if text else None

    @render.ui
    def investor_metrics():
        data = statements()
        tickers = list(data["infos"])
        sections = investor_metrics_fn(data["infos"], data["annuals"])
        head = ui.tags.tr(
            ui.tags.th("Metric"),
            *[
                ui.tags.th(ui.span(class_="dot", style=f"background:{PALETTE[i % len(PALETTE)]}"), t)
                for i, t in enumerate(tickers)
            ],
        )
        cards = []
        for title, rows in sections:
            body = [
                ui.tags.tr(
                    ui.tags.td(label, title=f"{GLOSSARY[label][0]} {GLOSSARY[label][1]}", class_="has-tip"),
                    *[ui.tags.td(text, class_=f"flag-{flag}" if flag else "") for text, flag in cells],
                )
                for label, cells in rows
            ]
            cards.append(
                ui.card(
                    card_header(SECTION_ICONS.get(title, "list-ul"), title, SECTION_INTRO[title]),
                    ui.div(
                        ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*body), class_="table fundamentals-table"),
                        class_="table-responsive",
                    ),
                )
            )
        legend = ui.div(
            ui.span("healthy", class_="flag-chip flag-good"),
            ui.span("worth a closer look", class_="flag-chip flag-bad"),
            " — rule-of-thumb cues only; what's normal varies by industry. Figures are the latest fiscal year "
            "or Yahoo's trailing-twelve-month data, whichever the metric uses.",
            class_="section-sub mb-2",
        )
        return ui.TagList(legend, *cards)


app = App(app_ui, server)
