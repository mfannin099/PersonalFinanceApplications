"""Yahoo Finance access via ``yfinance``, plus ticker parsing.

``parse_tickers`` and ``rebase_to_100`` are pure. The ``fetch_*`` functions hit
the network and are cached in-process, keyed by today's date so a long-running
server doesn't serve yesterday's prices forever.
"""

import re
from datetime import date
from functools import lru_cache

import pandas as pd
import yfinance as yf

MAX_TICKERS = 5
PERIODS = {"6mo": "6 months", "1y": "1 year", "2y": "2 years", "5y": "5 years", "custom": "Custom range"}
_TICKER_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")


def parse_tickers(text: str, limit: int = MAX_TICKERS) -> tuple[list[str], list[str]]:
    """Split comma/space-separated input into ``(valid tickers, rejected tokens)``.

    Tickers are upper-cased and de-duplicated in input order. Tokens that
    don't look like a ticker symbol, or that exceed ``limit``, are rejected.
    """
    seen, valid, rejected = set(), [], []
    for token in re.split(r"[,\s]+", text or ""):
        symbol = token.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        if not _TICKER_PATTERN.match(symbol) or len(valid) >= limit:
            rejected.append(symbol)
        else:
            valid.append(symbol)
    return valid, rejected


def rebase_to_100(closes: pd.DataFrame) -> pd.DataFrame:
    """Each column scaled so its first available price is 100, for comparing growth."""
    return closes.apply(lambda col: col / col.dropna().iloc[0] * 100 if col.notna().any() else col)


@lru_cache(maxsize=64)
def _download(tickers: tuple[str, ...], period: str | None, start, end, _day: date) -> pd.DataFrame:
    kwargs = {"start": start, "end": end} if period is None else {"period": period}
    return yf.download(list(tickers), auto_adjust=True, progress=False, group_by="column", **kwargs)


def fetch_closes(
    tickers: list[str], period: str | None = None, start=None, end=None
) -> tuple[pd.DataFrame, list[str]]:
    """Adjusted daily closes, one column per ticker, plus the tickers Yahoo had no data for.

    Pass either ``period`` (a yfinance period string like ``"1y"``) or
    ``start``/``end`` dates. Unknown tickers come back from Yahoo as all-NaN
    columns rather than errors, so they're dropped here and reported.
    """
    if not tickers:
        return pd.DataFrame(), []
    raw = _download(tuple(tickers), period, start, end, date.today())
    if raw.empty:
        return pd.DataFrame(), list(tickers)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if not isinstance(closes, pd.DataFrame):
        closes = closes.to_frame()
    if list(closes.columns) == ["Close"]:
        closes.columns = list(tickers)
    closes = closes.reindex(columns=[t for t in tickers if t in closes.columns])
    missing = [t for t in tickers if t not in closes.columns or closes[t].isna().all()]
    return closes.drop(columns=[t for t in missing if t in closes.columns]), missing


@lru_cache(maxsize=64)
def _info(ticker: str, _day: date) -> dict:
    try:
        return dict(yf.Ticker(ticker).info or {})
    except Exception:  # yfinance raises a variety of HTTP/parse errors; fundamentals are best-effort
        return {}


def fetch_info(ticker: str) -> dict:
    """Yahoo's company info for ``ticker``; an empty dict if it can't be fetched."""
    return _info(ticker, date.today())
