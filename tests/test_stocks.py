"""Tests for Stocks/ — synthetic prices and info dicts only, never the network."""

import numpy as np
import pandas as pd
import pytest

from Stocks.forecast import crossovers, ma_trend_forecast, moving_average
from Stocks.fundamentals import MISSING, fundamentals_table
from Stocks.market_data import parse_tickers, rebase_to_100


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


def test_rebase_to_100_uses_each_columns_first_price():
    closes = pd.DataFrame({"X": [50.0, 75.0], "Y": [np.nan, 10.0]})
    rebased = rebase_to_100(closes)
    assert rebased["X"].tolist() == [100.0, 150.0]
    assert rebased["Y"].iloc[1] == 100.0
