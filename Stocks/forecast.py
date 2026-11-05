"""Moving averages, crossovers and a moving-average trend forecast — pure, Shiny-free.

The forecast is deliberately simple and explainable, not a predictive model:
it extends the recent slope of a moving average forward from the last close,
and wraps it in a band sized by the stock's recent day-to-day volatility.
"""

import numpy as np
import pandas as pd

MA_KINDS = ("SMA", "EMA")
BAND_Z = 1.2816  # two-sided 80% band under a normal approximation
BAND_LABEL = "80% band"
VOLATILITY_LOOKBACK_DAYS = 60


def moving_average(close: pd.Series, window: int, kind: str = "SMA") -> pd.Series:
    """Simple or exponential moving average; the first ``window - 1`` values are NaN either way."""
    if kind == "SMA":
        return close.rolling(window).mean()
    if kind == "EMA":
        return close.ewm(span=window, adjust=False, min_periods=window).mean()
    raise ValueError(f"Unknown moving-average kind: {kind!r}")


def crossovers(short_ma: pd.Series, long_ma: pd.Series) -> pd.DataFrame:
    """Dates where the short MA crosses the long one.

    ``Signal`` is ``"Bullish"`` when the short MA moves above the long MA (a
    "golden cross") and ``"Bearish"`` when it moves below (a "death cross").
    """
    diff = (short_ma - long_ma).dropna()
    side = np.sign(diff)
    side = side[side != 0]  # a tie isn't a cross; compare against the last real side
    changed = side.ne(side.shift()) & side.shift().notna()
    crosses = side[changed]
    return pd.DataFrame(
        {
            "Date": crosses.index,
            "Signal": np.where(crosses > 0, "Bullish", "Bearish"),
            "Level": short_ma.loc[crosses.index].to_numpy(),
        }
    )


def ma_trend_forecast(
    close: pd.Series, window: int, horizon: int, kind: str = "SMA"
) -> pd.DataFrame:
    """Project ``horizon`` business days ahead along the moving average's recent trend.

    The daily growth rate is the slope of a straight-line fit to log(MA) over
    its last ``window`` points, so the projection compounds and can't go
    negative. It starts from the last close (not the MA, which lags price).
    The band uses the standard deviation of daily log returns over the last
    ``VOLATILITY_LOOKBACK_DAYS``, widening with the square root of time.

    Returns a frame indexed by future business dates with ``Forecast``,
    ``Lower`` and ``Upper`` columns. Raises ``ValueError`` when there isn't
    enough history for the chosen window.
    """
    close = close.dropna()
    if horizon < 1:
        raise ValueError("Forecast horizon must be at least 1 day.")
    ma = moving_average(close, window, kind).dropna()
    if len(ma) < window or len(close) < 3:
        raise ValueError(
            f"Not enough history for a {window}-day {kind} trend — choose a longer period or a shorter window."
        )

    recent = np.log(ma.iloc[-window:].to_numpy())
    daily_growth = np.polyfit(np.arange(window), recent, 1)[0]

    log_returns = np.log(close).diff().dropna().iloc[-VOLATILITY_LOOKBACK_DAYS:]
    daily_vol = float(log_returns.std()) if len(log_returns) > 1 else 0.0

    steps = np.arange(1, horizon + 1)
    forecast = close.iloc[-1] * np.exp(daily_growth * steps)
    spread = np.exp(BAND_Z * daily_vol * np.sqrt(steps))
    dates = pd.bdate_range(close.index[-1] + pd.offsets.BDay(1), periods=horizon)
    return pd.DataFrame({"Forecast": forecast, "Lower": forecast / spread, "Upper": forecast * spread}, index=dates)
