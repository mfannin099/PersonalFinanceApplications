"""Investor metrics from financial statements and Yahoo's ``info`` dict — pure, Shiny-free.

``annual_table`` turns yfinance's income statement, balance sheet and cash-flow
frames into one tidy per-fiscal-year table (with margins, growth and returns).
``investor_metrics`` then lays latest-year and TTM figures out as grouped
sections, one column per ticker, with a simple healthy/flag cue per cell.

The cues are common rules of thumb (e.g. current ratio under 1), not advice —
what counts as healthy varies a lot by industry.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd

MISSING = "—"
GOOD, BAD = "good", "bad"
DEFAULT_TAX_RATE = 0.21

# annual-table column -> (statement, Yahoo row names tried in order)
_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Revenue": ("income", ("Total Revenue",)),
    "Gross profit": ("income", ("Gross Profit",)),
    "Operating income": ("income", ("Operating Income",)),
    "EBIT": ("income", ("EBIT",)),
    "Net income": ("income", ("Net Income",)),
    "Diluted EPS": ("income", ("Diluted EPS",)),
    "Diluted shares": ("income", ("Diluted Average Shares",)),
    "Interest expense": ("income", ("Interest Expense",)),
    "Tax rate": ("income", ("Tax Rate For Calcs",)),
    "Total debt": ("balance", ("Total Debt",)),
    "Cash": ("balance", ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")),
    "Equity": ("balance", ("Stockholders Equity", "Common Stock Equity")),
    "Total assets": ("balance", ("Total Assets",)),
    "Invested capital": ("balance", ("Invested Capital",)),
    "Operating cash flow": ("cashflow", ("Operating Cash Flow",)),
    "Capex": ("cashflow", ("Capital Expenditure",)),
    "Free cash flow": ("cashflow", ("Free Cash Flow",)),
    "Buybacks": ("cashflow", ("Repurchase Of Capital Stock",)),
}


def _row(stmt: pd.DataFrame | None, names: tuple[str, ...]) -> pd.Series:
    """First matching row of a yfinance statement (rows = items, columns = dates), oldest first."""
    if stmt is not None and not stmt.empty:
        for name in names:
            if name in stmt.index:
                return pd.to_numeric(stmt.loc[name], errors="coerce").sort_index()
    return pd.Series(dtype=float)


def _div(num: pd.Series, den: pd.Series) -> pd.Series:
    """Element-wise ratio that is NaN (not inf) where the denominator is zero or missing."""
    return num / den.where(den != 0)


def annual_table(income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame) -> pd.DataFrame:
    """One row per fiscal year (oldest first), raw line items followed by derived ratios.

    Missing statements or line items just leave NaN columns; an empty frame comes back when
    nothing usable was provided. Ratios are fractions (0.25 = 25%), except ``Interest coverage``
    and ``Debt/equity`` which are plain multiples.
    """
    statements = {"income": income, "balance": balance, "cashflow": cashflow}
    rows = {col: _row(statements[stmt], names) for col, (stmt, names) in _SOURCES.items()}
    rows = {col: row for col, row in rows.items() if not row.empty}
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(rows).sort_index().dropna(how="all")
    table = table.reindex(columns=list(_SOURCES))  # keep absent line items as NaN columns
    if table.empty:
        return table

    prev_ni = table["Net income"].shift()
    tax = table["Tax rate"].clip(0, 0.5).fillna(DEFAULT_TAX_RATE)
    ni = table["Net income"]
    table["Gross margin"] = _div(table["Gross profit"], table["Revenue"])
    table["Operating margin"] = _div(table["Operating income"], table["Revenue"])
    table["Net margin"] = _div(ni, table["Revenue"])
    table["Revenue growth"] = table["Revenue"].pct_change(fill_method=None)
    table["Net income growth"] = _div(ni - prev_ni, prev_ni.abs())
    table["FCF conversion"] = _div(table["Free cash flow"], ni.where(ni > 0))
    table["Interest coverage"] = _div(table["EBIT"], table["Interest expense"].abs())
    table["Debt/equity"] = _div(table["Total debt"], table["Equity"].where(table["Equity"] > 0))
    table["ROE"] = _div(ni, table["Equity"].where(table["Equity"] > 0))
    table["ROA"] = _div(ni, table["Total assets"])
    table["ROIC"] = _div(table["EBIT"] * (1 - tax), table["Invested capital"])
    return table.replace([np.inf, -np.inf], np.nan)


# --- formatting ---------------------------------------------------------------


def _compact_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= threshold:
            return f"{sign}${value / threshold:,.2f}{suffix}"
    return f"{sign}${value:,.2f}"


def _money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _multiple(value: float) -> str:
    return f"{value:,.1f}x"


def _ratio(value: float) -> str:
    return f"{value:,.2f}"


def _pct_change(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _fy_end(value: pd.Timestamp) -> str:
    return f"{value:%b %Y}"


# --- row specs ----------------------------------------------------------------

Getter = Callable[[dict, pd.DataFrame], object]
Rule = Callable[[float], str | None]


def _latest(annual: pd.DataFrame, col: str) -> float | None:
    if annual is None or annual.empty or col not in annual:
        return None
    values = annual[col].dropna()
    return float(values.iloc[-1]) if not values.empty else None


def _fy(col: str) -> Getter:
    return lambda info, annual: _latest(annual, col)


def _from_info(key: str) -> Getter:
    return lambda info, annual: info.get(key)


def _info_then_fy(key: str, col: str) -> Getter:
    """Yahoo's trailing figure when present, otherwise the latest fiscal-year one."""

    def get(info: dict, annual: pd.DataFrame):
        value = info.get(key)
        return value if _is_number(value) else _latest(annual, col)

    return get


def _fiscal_year_end(info: dict, annual: pd.DataFrame):
    return annual.index[-1] if annual is not None and not annual.empty else None


def _revenue_cagr(info: dict, annual: pd.DataFrame):
    if annual is None or annual.empty:
        return None
    revenue = annual["Revenue"].dropna()
    if len(revenue) < 2 or revenue.iloc[0] <= 0 or revenue.iloc[-1] <= 0:
        return None
    years = (revenue.index[-1] - revenue.index[0]).days / 365.25
    return (revenue.iloc[-1] / revenue.iloc[0]) ** (1 / years) - 1 if years > 0 else None


def _share_count_change(info: dict, annual: pd.DataFrame):
    if annual is None or annual.empty:
        return None
    shares = annual["Diluted shares"].dropna()
    if len(shares) < 2 or shares.iloc[0] <= 0:
        return None
    return shares.iloc[-1] / shares.iloc[0] - 1


def _net_debt(info: dict, annual: pd.DataFrame):
    debt, cash = _latest(annual, "Total debt"), _latest(annual, "Cash")
    return None if debt is None or cash is None else debt - cash


def _fcf_yield(info: dict, annual: pd.DataFrame):
    fcf = info.get("freeCashflow")
    fcf = fcf if _is_number(fcf) else _latest(annual, "Free cash flow")
    cap = info.get("marketCap")
    return fcf / cap if _is_number(fcf) and _is_number(cap) and cap else None


def _debt_to_equity(info: dict, annual: pd.DataFrame):
    value = _latest(annual, "Debt/equity")
    if value is not None:
        return value
    reported = info.get("debtToEquity")  # Yahoo reports this as a percent (84.3 = 0.84x)
    return reported / 100 if _is_number(reported) else None


def _analyst_upside(info: dict, annual: pd.DataFrame):
    target = info.get("targetMeanPrice")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    return target / price - 1 if _is_number(target) and _is_number(price) and price else None


def _recommendation(info: dict, annual: pd.DataFrame):
    key = info.get("recommendationKey")
    return key.replace("_", " ").title() if isinstance(key, str) and key.lower() != "none" else None


def _is_number(value) -> bool:
    return isinstance(value, int | float | np.integer | np.floating) and bool(np.isfinite(value))


def _at_least(good: float, bad: float | None = None) -> Rule:
    """Healthy at/above ``good``; flagged below ``bad`` (when given)."""
    return lambda v: GOOD if v >= good else (BAD if bad is not None and v < bad else None)


def _at_most(good: float, bad: float) -> Rule:
    """Healthy at/below ``good``; flagged above ``bad``."""
    return lambda v: GOOD if v <= good else (BAD if v > bad else None)


def _sign(v: float) -> str | None:
    return GOOD if v > 0 else (BAD if v < 0 else None)


def _share_rule(v: float) -> str | None:
    return GOOD if v <= -0.01 else (BAD if v >= 0.02 else None)


# (label, getter, formatter, rule) — grouped into the sections shown in the app.
SECTIONS: list[tuple[str, list[tuple[str, Getter, Callable, Rule | None]]]] = [
    (
        "Income statement",
        [
            ("Latest fiscal year", _fiscal_year_end, _fy_end, None),
            ("Revenue", _info_then_fy("totalRevenue", "Revenue"), _compact_money, None),
            ("Gross profit", _info_then_fy("grossProfits", "Gross profit"), _compact_money, None),
            ("Operating income", _fy("Operating income"), _compact_money, _sign),
            ("Net income", _info_then_fy("netIncomeToCommon", "Net income"), _compact_money, _sign),
            ("EBITDA", _from_info("ebitda"), _compact_money, None),
            ("Diluted EPS", _info_then_fy("trailingEps", "Diluted EPS"), _money, _sign),
        ],
    ),
    (
        "Growth",
        [
            ("Revenue growth (last FY)", _fy("Revenue growth"), _pct_change, _sign),
            ("Net income growth (last FY)", _fy("Net income growth"), _pct_change, _sign),
            ("Revenue CAGR (years shown)", _revenue_cagr, _pct_change, _sign),
        ],
    ),
    (
        "Profitability",
        [
            ("Gross margin", _info_then_fy("grossMargins", "Gross margin"), _pct, None),
            ("Operating margin", _info_then_fy("operatingMargins", "Operating margin"), _pct, _sign),
            ("Net margin", _info_then_fy("profitMargins", "Net margin"), _pct, _sign),
            ("Return on equity", _info_then_fy("returnOnEquity", "ROE"), _pct, _at_least(0.15, 0.05)),
            ("Return on assets", _info_then_fy("returnOnAssets", "ROA"), _pct, _at_least(0.05, 0.01)),
            ("Return on invested capital", _fy("ROIC"), _pct, _at_least(0.10, 0.05)),
        ],
    ),
    (
        "Valuation",
        [
            ("P/E (trailing)", _from_info("trailingPE"), _multiple, None),
            ("P/E (forward)", _from_info("forwardPE"), _multiple, None),
            ("PEG ratio", _from_info("trailingPegRatio"), _ratio, _at_most(1, 2)),
            ("Price / sales", _from_info("priceToSalesTrailing12Months"), _multiple, None),
            ("Price / book", _from_info("priceToBook"), _multiple, None),
            ("EV / EBITDA", _from_info("enterpriseToEbitda"), _multiple, None),
            ("Free cash flow yield", _fcf_yield, _pct, _at_least(0.04, 0)),
        ],
    ),
    (
        "Financial health",
        [
            ("Debt / equity", _debt_to_equity, _multiple, _at_most(1, 2)),
            ("Current ratio", _info_then_fy("currentRatio", "Current ratio"), _ratio, _at_least(1.5, 1)),
            ("Interest coverage", _fy("Interest coverage"), _multiple, _at_least(5, 2)),
            ("Total debt", _info_then_fy("totalDebt", "Total debt"), _compact_money, None),
            ("Cash & short-term investments", _info_then_fy("totalCash", "Cash"), _compact_money, None),
            ("Net debt (last FY)", _net_debt, _compact_money, None),
        ],
    ),
    (
        "Cash flow",
        [
            ("Operating cash flow", _info_then_fy("operatingCashflow", "Operating cash flow"), _compact_money, _sign),
            ("Capital expenditure (last FY)", _fy("Capex"), _compact_money, None),
            ("Free cash flow", _info_then_fy("freeCashflow", "Free cash flow"), _compact_money, _sign),
            ("FCF conversion (FCF / net income)", _fy("FCF conversion"), _pct, _at_least(0.8, 0.5)),
        ],
    ),
    (
        "Shareholder return",
        [
            ("Dividend yield", _from_info("dividendYield"), lambda v: f"{v:.2f}%", None),
            ("Payout ratio", _from_info("payoutRatio"), _pct, _at_most(0.6, 1)),
            ("Buybacks (last FY)", _fy("Buybacks"), lambda v: _compact_money(abs(v)), None),
            ("Share count change (years shown)", _share_count_change, _pct_change, _share_rule),
        ],
    ),
    (
        "Analyst view",
        [
            ("Mean price target", _from_info("targetMeanPrice"), _money, None),
            ("Upside to target", _analyst_upside, _pct_change, _at_least(0.10, -0.10)),
            ("Recommendation", _recommendation, str, None),
        ],
    ),
]

Cell = tuple[str, str | None]  # (display text, GOOD / BAD / None)


def investor_metrics(
    infos: dict[str, dict], annuals: dict[str, pd.DataFrame]
) -> list[tuple[str, list[tuple[str, list[Cell]]]]]:
    """Grouped metric rows, one cell per ticker, as ``[(section, [(label, [cell, ...])])]``.

    Anything Yahoo doesn't report shows as ``MISSING`` with no cue.
    """
    sections = []
    for title, rows in SECTIONS:
        built = []
        for label, getter, fmt, rule in rows:
            cells: list[Cell] = []
            for ticker, info in infos.items():
                value = getter(info, annuals.get(ticker, pd.DataFrame()))
                if value is None or (not isinstance(value, str | pd.Timestamp) and not _is_number(value)):
                    cells.append((MISSING, None))
                else:
                    flag = rule(value) if rule and _is_number(value) else None
                    cells.append((fmt(value), flag))
            built.append((label, cells))
        sections.append((title, built))
    return sections
