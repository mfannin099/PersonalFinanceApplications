"""Company fundamentals from Yahoo Finance's ``info`` dict, as a display table — pure, Shiny-free."""

from collections.abc import Callable

import pandas as pd

MISSING = "—"


def _compact_money(value: float) -> str:
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.2f}"


def _multiple(value: float) -> str:
    return f"{value:,.1f}x"


def _money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def _fraction_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _already_pct(value: float) -> str:
    # yfinance 1.x (checked on 1.8) reports dividendYield as a percent (0.33 means 0.33%),
    # unlike profitMargins, which is still a fraction.
    return f"{value:.2f}%"


def _plain(value) -> str:
    return f"{value:,.2f}" if isinstance(value, float) else str(value)


def _range(info: dict) -> str | None:
    low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    if low is None or high is None:
        return None
    return f"{_money(low)} – {_money(high)}"


# (row label, info key or a function of the whole info dict, formatter)
FIELDS: list[tuple[str, str | Callable[[dict], str | None], Callable]] = [
    ("Company", "shortName", str),
    ("Sector", "sector", str),
    ("Industry", "industry", str),
    ("Market cap", "marketCap", _compact_money),
    ("P/E (trailing)", "trailingPE", _multiple),
    ("P/E (forward)", "forwardPE", _multiple),
    ("EPS (trailing)", "trailingEps", _money),
    ("Dividend yield", "dividendYield", _already_pct),
    ("Beta", "beta", _plain),
    ("52-week range", _range, str),
    ("Revenue (TTM)", "totalRevenue", _compact_money),
    ("Profit margin", "profitMargins", _fraction_pct),
]


def fundamentals_table(infos: dict[str, dict]) -> pd.DataFrame:
    """One row per metric in ``FIELDS``, one column per ticker, formatted for display.

    A field Yahoo doesn't report (or reports as ``None``/NaN) shows as ``MISSING``.
    """
    columns = {}
    for ticker, info in infos.items():
        values = []
        for _, source, fmt in FIELDS:
            raw = source(info) if callable(source) else info.get(source)
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                values.append(MISSING)
            else:
                values.append(fmt(raw))
        columns[ticker] = values
    return pd.DataFrame(columns, index=[label for label, _, _ in FIELDS])
