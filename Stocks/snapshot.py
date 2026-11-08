"""Per-ticker price snapshot (latest price, day/period change, range position) and sparklines — pure, Shiny-free."""

import numpy as np
import pandas as pd

SPARK_POINTS = 60  # most recent closes drawn in the sparkline


def price_snapshot(closes: pd.DataFrame) -> pd.DataFrame:
    """One row per ticker with the figures a quick-glance card needs.

    Columns: ``Last``, ``As of``, ``Day change`` (vs. the previous close), ``Period return``
    (vs. the first close loaded), ``Low`` / ``High`` over the loaded period, and ``Range position``
    (0 = at the period low, 1 = at the high; NaN when the price never moved). Tickers with no
    prices are omitted; a ticker with a single price has NaN changes.
    """
    rows = {}
    for ticker in closes.columns:
        series = closes[ticker].dropna()
        if series.empty:
            continue
        last, low, high = float(series.iloc[-1]), float(series.min()), float(series.max())
        rows[ticker] = {
            "Last": last,
            "As of": series.index[-1],
            "Day change": last / series.iloc[-2] - 1 if len(series) > 1 else np.nan,
            "Period return": last / series.iloc[0] - 1 if len(series) > 1 else np.nan,
            "Low": low,
            "High": high,
            "Range position": (last - low) / (high - low) if high > low else np.nan,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def sparkline_points(values, width: int = 120, height: int = 36, pad: int = 2) -> str:
    """SVG ``points`` string for a polyline of the last ``SPARK_POINTS`` values, scaled to fit.

    Flat or too-short input gives a level line (or an empty string for fewer than 2 points),
    so callers never divide by zero.
    """
    data = np.asarray(pd.Series(values).dropna().tail(SPARK_POINTS), dtype=float)
    if len(data) < 2:
        return ""
    low, high = data.min(), data.max()
    span = high - low
    xs = np.linspace(pad, width - pad, len(data))
    ys = np.full(len(data), height / 2) if span == 0 else height - pad - (data - low) / span * (height - 2 * pad)
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
