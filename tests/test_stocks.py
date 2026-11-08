"""Tests for Stocks/ — synthetic prices and info dicts only, never the network."""

import numpy as np
import pandas as pd
import pytest

from Stocks.forecast import crossovers, ma_trend_forecast, moving_average
from Stocks.fundamentals import MISSING, fundamentals_table
from Stocks.market_data import START_VALUE, parse_tickers, rebase


def _prices(values) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2026-01-02", periods=len(values)), dtype=float)


# --- moving averages ---------------------------------------------------------


def test_sma_matches_hand_computed_mean():
    ma = moving_average(_prices([1, 2, 3, 4, 5]), 3, "SMA")
    assert ma.isna().sum() == 2
    assert ma.iloc[-1] == pytest.approx(4.0)


def test_ema_has_same_warmup_and_weights_recent_prices_more():
    prices = _prices([10] * 10 + [20])
    sma, ema = moving_average(prices, 5, "SMA"), moving_average(prices, 5, "EMA")
    assert ema.isna().sum() == sma.isna().sum() == 4
    assert ema.iloc[-1] > sma.iloc[-1]  # 13.33 vs 12.0: EMA reacts faster to the jump


def test_unknown_ma_kind_raises():
    with pytest.raises(ValueError):
        moving_average(_prices([1, 2, 3]), 2, "WMA")


# --- crossovers --------------------------------------------------------------


def test_crossovers_detects_bullish_then_bearish():
    short = _prices([1, 1, 3, 3, 1, 1])
    long = _prices([2, 2, 2, 2, 2, 2])
    result = crossovers(short, long)
    assert list(result["Signal"]) == ["Bullish", "Bearish"]
    assert list(result["Date"]) == [short.index[2], short.index[4]]


def test_crossovers_ignores_touching_without_crossing():
    short = _prices([1, 2, 1])  # touches the long MA at 2 but never goes above it
    long = _prices([2, 2, 2])
    assert crossovers(short, long).empty


# --- forecast ----------------------------------------------------------------


def test_forecast_follows_steady_growth_and_starts_at_last_close():
    prices = _prices(100 * 1.01 ** np.arange(80))  # exactly +1%/day
    result = ma_trend_forecast(prices, window=20, horizon=10)
    assert len(result) == 10
    assert result.index[0] > prices.index[-1]
    assert result["Forecast"].iloc[0] == pytest.approx(prices.iloc[-1] * 1.01, rel=1e-6)
    assert result["Forecast"].iloc[-1] == pytest.approx(prices.iloc[-1] * 1.01**10, rel=1e-6)


def test_forecast_band_brackets_forecast_and_widens():
    rng = np.random.default_rng(0)
    prices = _prices(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))))
    result = ma_trend_forecast(prices, window=20, horizon=30, kind="EMA")
    assert (result["Lower"] < result["Forecast"]).all() and (result["Forecast"] < result["Upper"]).all()
    width = result["Upper"] - result["Lower"]
    assert width.iloc[-1] > width.iloc[0]
    assert (result["Lower"] > 0).all()


def test_forecast_needs_enough_history():
    with pytest.raises(ValueError, match="Not enough history"):
        ma_trend_forecast(_prices(range(1, 30)), window=20, horizon=5)


# --- fundamentals ------------------------------------------------------------


def test_fundamentals_table_formats_and_fills_missing():
    infos = {
        "AAA": {
            "shortName": "Alpha Inc.",
            "marketCap": 5_079_143_284_736,
            "trailingPE": 38.92,
            "trailingEps": 8.98,
            "dividendYield": 0.33,
            "profitMargins": 0.27604,
            "fiftyTwoWeekLow": 243.42,
            "fiftyTwoWeekHigh": 354.47,
        },
        "BBB": {},
    }
    table = fundamentals_table(infos)
    assert list(table.columns) == ["AAA", "BBB"]
    assert table.loc["Market cap", "AAA"] == "$5.08T"
    assert table.loc["P/E (trailing)", "AAA"] == "38.9x"
    assert table.loc["Dividend yield", "AAA"] == "0.33%"
    assert table.loc["Profit margin", "AAA"] == "27.6%"
    assert table.loc["52-week range", "AAA"] == "$243.42 – $354.47"
    assert table.loc["P/E (forward)", "AAA"] == MISSING
    assert (table["BBB"] == MISSING).all()


# --- market data helpers -----------------------------------------------------


def test_parse_tickers_normalizes_dedupes_and_limits():
    valid, rejected = parse_tickers(" aapl, msft MSFT,brk-b, ^gspc, bad$sym, a,b,c ", limit=5)
    assert valid == ["AAPL", "MSFT", "BRK-B", "^GSPC", "A"]
    assert rejected == ["BAD$SYM", "B", "C"]


def test_rebase_uses_each_columns_first_price_and_starts_at_1000():
    closes = pd.DataFrame({"X": [50.0, 75.0], "Y": [np.nan, 10.0]})
    rebased = rebase(closes)
    assert START_VALUE == 1000
    assert rebased["X"].tolist() == [1000.0, 1500.0]
    assert rebased["Y"].iloc[1] == 1000.0
    assert rebase(closes, start=100)["X"].tolist() == [100.0, 150.0]


# --- financials ----------------------------------------------------------------

from Stocks.financials import BAD, GOOD, MISSING as FIN_MISSING, annual_table, investor_metrics  # noqa: E402


def _stmt(**rows) -> pd.DataFrame:
    """A yfinance-style statement: rows = items, columns = fiscal year ends, newest first."""
    dates = pd.to_datetime(["2025-09-30", "2024-09-30", "2023-09-30"])
    return pd.DataFrame(rows, index=dates).T


INCOME = _stmt(
    **{
        "Total Revenue": [200.0, 150.0, 100.0],
        "Gross Profit": [100.0, 60.0, 40.0],
        "Operating Income": [50.0, 30.0, 10.0],
        "EBIT": [50.0, 30.0, 10.0],
        "Net Income": [40.0, 20.0, 10.0],
        "Interest Expense": [-10.0, -10.0, -10.0],
        "Diluted Average Shares": [90.0, 95.0, 100.0],
    }
)
BALANCE = _stmt(
    **{"Total Debt": [80.0, 80.0, 80.0], "Stockholders Equity": [100.0, 80.0, 60.0], "Total Assets": [400.0, 300.0, 200.0]}
)
CASHFLOW = _stmt(**{"Free Cash Flow": [36.0, 18.0, 5.0], "Repurchase Of Capital Stock": [-30.0, -20.0, -10.0]})


def test_annual_table_orders_oldest_first_and_computes_ratios():
    table = annual_table(INCOME, BALANCE, CASHFLOW)
    assert list(table.index.year) == [2023, 2024, 2025]
    latest = table.iloc[-1]
    assert latest["Gross margin"] == pytest.approx(0.5)
    assert latest["Net margin"] == pytest.approx(0.2)
    assert latest["Revenue growth"] == pytest.approx(200 / 150 - 1)
    assert latest["Net income growth"] == pytest.approx(1.0)
    assert latest["FCF conversion"] == pytest.approx(0.9)
    assert latest["Interest coverage"] == pytest.approx(5.0)
    assert latest["Debt/equity"] == pytest.approx(0.8)
    assert latest["ROE"] == pytest.approx(0.4)
    assert pd.isna(table.iloc[0]["Revenue growth"])


def test_annual_table_handles_empty_and_missing_items():
    assert annual_table(pd.DataFrame(), pd.DataFrame(), pd.DataFrame()).empty
    table = annual_table(INCOME, pd.DataFrame(), pd.DataFrame())  # no balance sheet / cash flow
    assert pd.isna(table.iloc[-1]["Debt/equity"]) and pd.isna(table.iloc[-1]["ROE"])
    assert table.iloc[-1]["Net margin"] == pytest.approx(0.2)


def test_annual_table_zero_denominators_give_nan_not_inf():
    income = _stmt(**{"Total Revenue": [0.0, 0.0, 0.0], "Net Income": [5.0, 5.0, 5.0], "EBIT": [5.0, 5.0, 5.0],
                      "Interest Expense": [0.0, 0.0, 0.0]})
    table = annual_table(income, pd.DataFrame(), pd.DataFrame())
    assert table["Net margin"].isna().all() and table["Interest coverage"].isna().all()


def _cells(sections, label):
    for _, rows in sections:
        for row_label, cells in rows:
            if row_label == label:
                return cells
    raise KeyError(label)


def test_investor_metrics_formats_values_and_flags_health():
    annual = annual_table(INCOME, BALANCE, CASHFLOW)
    info = {"currentRatio": 0.8, "dividendYield": 0.5, "targetMeanPrice": 120.0, "currentPrice": 100.0}
    sections = investor_metrics({"AAA": info}, {"AAA": annual})
    assert _cells(sections, "Current ratio") == [("0.80", BAD)]
    assert _cells(sections, "Interest coverage") == [("5.0x", GOOD)]
    assert _cells(sections, "Net margin") == [("20.0%", GOOD)]
    assert _cells(sections, "Revenue growth (last FY)")[0][0] == "+33.3%"
    assert _cells(sections, "Share count change (years shown)") == [("-10.0%", GOOD)]
    assert _cells(sections, "Upside to target") == [("+20.0%", GOOD)]
    assert _cells(sections, "Dividend yield") == [("0.50%", None)]


def test_investor_metrics_missing_data_shows_dash_without_cue():
    sections = investor_metrics({"ZZZ": {}}, {"ZZZ": pd.DataFrame()})
    assert _cells(sections, "P/E (trailing)") == [(FIN_MISSING, None)]
    assert _cells(sections, "Revenue growth (last FY)") == [(FIN_MISSING, None)]
    assert _cells(sections, "Recommendation") == [(FIN_MISSING, None)]
    assert all(len(cells) == 1 for _, rows in sections for _, cells in rows)


# --- ML forecast (XGBoost) -------------------------------------------------------

from Stocks.ml_forecast import FEATURES, build_features, forward_return, train_and_forecast, walk_forward  # noqa: E402


def _random_walk(n=700, seed=0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=pd.bdate_range("2021-01-04", periods=n))


def test_features_use_only_past_data():
    close = _random_walk()
    full = build_features(close)
    truncated = build_features(close.iloc[:400])
    common = truncated.dropna().index
    pd.testing.assert_frame_equal(full.loc[common], truncated.loc[common])  # future prices don't change past features
    assert list(full.columns) == FEATURES


def test_forward_return_is_log_change_over_horizon():
    close = _prices([100, 110, 121, 133.1])
    fwd = forward_return(close, 2)
    assert fwd.iloc[0] == pytest.approx(np.log(1.21))
    assert fwd.iloc[-2:].isna().all()


def test_walk_forward_trains_strictly_before_each_test_block_with_gap():
    close = _random_walk()
    feats = build_features(close).dropna()
    data = feats.join(forward_return(close, 20).rename("t")).dropna()
    oos = walk_forward(data[FEATURES], data["t"], horizon=20)
    assert oos.index.is_monotonic_increasing and oos.index.is_unique
    assert oos.index[0] > data.index[int(len(data) * 0.6) - 1]
    assert {"Predicted", "Actual", "Baseline"} <= set(oos.columns)


def test_train_and_forecast_returns_sane_result_and_is_deterministic():
    close = _random_walk()
    a, b = train_and_forecast(close, 20), train_and_forecast(close, 20)
    assert a["predicted_return"] == pytest.approx(b["predicted_return"])
    assert a["lower_price"] < a["predicted_price"] < a["upper_price"]
    assert a["target_date"] > a["as_of"]
    assert a["importance"].sum() == pytest.approx(1.0) and set(a["importance"].index) == set(FEATURES)
    assert -1 <= a["metrics"]["hit_rate"] <= 1 and a["metrics"]["test_days"] > 0
    assert isinstance(a["verdict"], str)


def test_train_and_forecast_rejects_short_history_and_bad_horizon():
    with pytest.raises(ValueError, match="longer lookback"):
        train_and_forecast(_random_walk(150), 20)
    with pytest.raises(ValueError):
        train_and_forecast(_random_walk(), 0)


def test_model_finds_planted_signal_in_noise():
    """Sanity check the pipeline can learn: returns driven by 21-day momentum should score positive skill."""
    rng = np.random.default_rng(1)
    n = 1200
    noise = rng.normal(0, 0.01, n)
    ret = np.zeros(n)
    for t in range(21, n):  # momentum: today's return follows the trailing 21-day drift
        ret[t] = 0.95 * ret[t - 21 : t].mean() + noise[t]
    close = pd.Series(100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2019-01-01", periods=n))
    res = train_and_forecast(close, 5)
    assert res["metrics"]["skill"] > 0 and res["metrics"]["correlation"] > 0


# --- ML tuning, feature selection -----------------------------------------------

from Stocks.ml_forecast import PARAM_BOUNDS, PARAMS, TUNE_GRID, clean_params  # noqa: E402


def test_clean_params_clamps_overrides_and_ignores_unknown_keys():
    cleaned = clean_params({"max_depth": 99, "learning_rate": 5, "reg_lambda": -3, "bogus": 1, "subsample": None})
    assert cleaned["max_depth"] == PARAM_BOUNDS["max_depth"][1]
    assert cleaned["learning_rate"] == PARAM_BOUNDS["learning_rate"][1]
    assert cleaned["reg_lambda"] == 0
    assert cleaned["subsample"] == PARAMS["subsample"] and "bogus" not in cleaned
    assert isinstance(clean_params({"n_estimators": 120.7})["n_estimators"], int)
    assert clean_params(None) == PARAMS


def test_feature_subset_limits_model_inputs_and_requires_at_least_one():
    close = _random_walk()
    res = train_and_forecast(close, 20, features=["mom_21", "vol_21"])
    assert res["features"] == ["mom_21", "vol_21"] and set(res["importance"].index) == {"mom_21", "vol_21"}
    with pytest.raises(ValueError, match="at least one feature"):
        train_and_forecast(close, 20, features=[])


def test_custom_params_are_used_and_reported():
    res = train_and_forecast(_random_walk(), 20, params={"max_depth": 3, "n_estimators": 80})
    assert res["params"]["max_depth"] == 3 and res["params"]["n_estimators"] == 80 and not res["auto_tuned"]
    assert res["train_mae"] >= 0


def test_auto_tune_picks_from_the_grid_and_leaves_other_params_alone():
    res = train_and_forecast(_random_walk(900), 10, auto_tune=True)
    assert res["auto_tuned"]
    assert {"max_depth": res["params"]["max_depth"], "learning_rate": res["params"]["learning_rate"]} in TUNE_GRID
    assert res["params"]["min_child_weight"] == PARAMS["min_child_weight"]


# --- financials reading guide ------------------------------------------------------

from Stocks.financials import GLOSSARY, READING_ORDER, SECTION_INTRO, SECTIONS  # noqa: E402


def test_glossary_covers_every_metric_row_and_nothing_extra():
    labels = [label for _, rows in SECTIONS for label, *_ in rows]
    assert set(labels) == set(GLOSSARY)
    assert all(meaning.strip() and reading.strip() for meaning, reading in GLOSSARY.values())


def test_every_section_has_an_intro_and_reading_order_names_real_sections():
    titles = [title for title, _ in SECTIONS]
    assert set(SECTION_INTRO) == set(titles)
    assert all(title in titles for title, _ in READING_ORDER)


# --- snapshot cards -----------------------------------------------------------------

from Stocks.snapshot import SPARK_POINTS, price_snapshot, sparkline_points  # noqa: E402


def test_price_snapshot_computes_changes_and_range_position():
    closes = pd.DataFrame({"A": [100.0, 110.0, 105.0, 120.0], "B": [np.nan, 50.0, 50.0, 50.0], "C": [np.nan] * 4})
    snap = price_snapshot(closes)
    assert list(snap.index) == ["A", "B"]  # all-NaN tickers are dropped
    a = snap.loc["A"]
    assert a["Last"] == 120 and a["Day change"] == pytest.approx(120 / 105 - 1)
    assert a["Period return"] == pytest.approx(0.2) and a["Range position"] == pytest.approx(1.0)
    assert (a["Low"], a["High"]) == (100, 120)
    assert pd.isna(snap.loc["B", "Range position"])  # flat price: no range to position within


def test_price_snapshot_single_price_has_nan_changes():
    snap = price_snapshot(pd.DataFrame({"A": [10.0]}))
    assert snap.loc["A", "Last"] == 10 and pd.isna(snap.loc["A", "Day change"])


def test_sparkline_points_scale_into_box_and_handle_degenerate_input():
    pts = [tuple(map(float, p.split(","))) for p in sparkline_points([1, 3, 2, 5], width=100, height=40, pad=2).split()]
    assert len(pts) == 4 and pts[0][0] == 2 and pts[-1][0] == 98
    assert min(y for _, y in pts) == 2 and max(y for _, y in pts) == 38  # lowest value at the bottom
    assert pts[3][1] < pts[0][1]  # higher value sits higher (smaller y)
    assert sparkline_points([5, 5, 5]).count(",") == 3  # flat series still draws a level line
    assert sparkline_points([1]) == "" and sparkline_points([]) == ""
    assert len(sparkline_points(range(500)).split()) == SPARK_POINTS
