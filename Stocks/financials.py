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


# --- reading guide ------------------------------------------------------------

# The question each section answers, in the order the app shows them.
SECTION_INTRO: dict[str, str] = {
    "Income statement": "How much does the company sell and keep? The income statement is the scoreboard for a period.",
    "Growth": "Is the business getting bigger? Growth is the main reason investors pay up for a stock.",
    "Profitability": "How much of each dollar of sales becomes profit, and how well does the company use the money invested in it?",
    "Valuation": "How expensive is the stock relative to what the company earns? A great company can still be a poor buy at the wrong price.",
    "Financial health": "Could the company survive a bad stretch? Debt and short-term cash decide how much room for error it has.",
    "Cash flow": "Is the profit real cash? Earnings can be shaped by accounting; cash is harder to fake.",
    "Shareholder return": "How does the company pay owners back — dividends, buybacks — and does it dilute them?",
    "Analyst view": "What do professional analysts expect? Useful context, but analysts are often wrong and often optimistic.",
}

# metric label -> (what it is, how to read it). Keys must match the row labels in SECTIONS (tested).
GLOSSARY: dict[str, tuple[str, str]] = {
    "Latest fiscal year": (
        "The month the company's most recent annual (fiscal) year ended. Companies pick their own year-end.",
        "Tickers can end in different months (e.g. Jun vs Sep), so annual figures cover slightly different periods.",
    ),
    "Revenue": (
        "Total sales before any costs ('the top line'). Shown as Yahoo's trailing twelve months when available.",
        "Bigger isn't automatically better — what matters is the trend (see Growth) and how much turns into profit.",
    ),
    "Gross profit": (
        "Revenue minus the direct cost of making what was sold (materials, hosting, manufacturing).",
        "Compare it with revenue (gross margin). It shows whether the product itself is profitable before overheads.",
    ),
    "Operating income": (
        "Gross profit minus running costs such as salaries, R&D and marketing — profit from the core business, "
        "before interest and taxes.",
        "Positive and growing is what you want. Negative means the core business loses money (common in young companies).",
    ),
    "Net income": (
        "The 'bottom line': profit after every cost, interest and tax. Shown as trailing twelve months when available.",
        "Positive is healthy. One-off items (asset sales, write-downs) can distort a single year, so look at several years.",
    ),
    "EBITDA": (
        "Earnings before interest, taxes, depreciation and amortisation — a rough proxy for operating cash earnings.",
        "Handy for comparing companies with different debt and tax situations. It ignores capital spending, so it can flatter "
        "heavy-spending businesses.",
    ),
    "Diluted EPS": (
        "Earnings per share: net income divided by all shares, counting options and convertibles that could be exercised.",
        "Rising EPS over time is the classic sign of a growing business. Compare with the share price via the P/E ratio.",
    ),
    "Revenue growth (last FY)": (
        "How much revenue changed in the latest fiscal year versus the year before.",
        "Green when positive. Mature large companies often grow 3–10%; fast growers 20%+. Compare against the same company's history.",
    ),
    "Net income growth (last FY)": (
        "How much net income changed versus the prior year.",
        "Profit growing faster than revenue means improving efficiency. Swings are large when the starting profit is small.",
    ),
    "Revenue CAGR (years shown)": (
        "Compound annual growth rate of revenue across the fiscal years in the chart — the smoothed yearly rate.",
        "Smooths out one-off years. If it's far below the last year's growth, recent growth may be accelerating (or a spike).",
    ),
    "Gross margin": (
        "Gross profit as a share of revenue.",
        "Higher means more pricing power or cheaper delivery. Software often runs 70%+, retailers 20–30% — compare within an industry.",
    ),
    "Operating margin": (
        "Operating income as a share of revenue.",
        "Shows how efficiently the whole business runs. Rising over time is a good sign; red when negative.",
    ),
    "Net margin": (
        "Net income as a share of revenue — cents of profit per dollar sold.",
        "Compare with peers. A very high margin can attract competition; red when the company loses money.",
    ),
    "Return on equity": (
        "Net income divided by shareholders' equity: profit generated on the owners' money.",
        "Green at 15%+, red below 5%. Debt and buybacks can inflate it (even above 100%), so read it alongside debt/equity.",
    ),
    "Return on assets": (
        "Net income divided by total assets: profit generated by everything the company owns.",
        "Green at 5%+, red below 1%. Asset-heavy industries (utilities, banks) naturally score lower.",
    ),
    "Return on invested capital": (
        "After-tax operating profit divided by the capital (debt plus equity) put into the business. Calculated here from the "
        "statements.",
        "Green at 10%+, red below 5%. Sustained ROIC well above the company's cost of capital is the hallmark of a strong business.",
    ),
    "P/E (trailing)": (
        "Share price divided by the last twelve months of earnings per share — dollars you pay per $1 of annual profit.",
        "Lower is cheaper, but low can mean the market expects trouble. The long-run market average is roughly 15–20; "
        "growth stocks run higher. Meaningless when earnings are negative.",
    ),
    "P/E (forward)": (
        "Share price divided by analysts' expected earnings for the next twelve months.",
        "Lower than the trailing P/E means earnings are expected to grow. It rests on forecasts that may not come true.",
    ),
    "PEG ratio": (
        "P/E divided by expected earnings growth rate — price adjusted for how fast profits are growing.",
        "Green at 1 or below (growth justifies the price), red above 2. A rough guide that depends on a growth forecast.",
    ),
    "Price / sales": (
        "Market value divided by revenue.",
        "Useful when profits are small or negative. Lower is cheaper, but compare within an industry — margins differ widely.",
    ),
    "Price / book": (
        "Market value divided by the accounting value of equity (assets minus liabilities).",
        "Under 1 can mean cheap or troubled. Asset-light companies (software, brands) naturally trade at high multiples.",
    ),
    "EV / EBITDA": (
        "Enterprise value (market value plus debt minus cash) divided by EBITDA — the whole business's price versus its "
        "operating earnings.",
        "Lower is cheaper. Treats debt fairly, so it's good for comparing companies with different balance sheets.",
    ),
    "Free cash flow yield": (
        "Free cash flow divided by market value — the cash return you'd get on the whole company at today's price.",
        "Green at 4%+ (compare with what safe bonds pay). Low yield means you're paying for future growth.",
    ),
    "Debt / equity": (
        "Total debt divided by shareholders' equity. Calculated from the latest annual balance sheet.",
        "Green at 1x or below, red above 2x. Utilities and banks run higher by design; negative equity makes it unreliable.",
    ),
    "Current ratio": (
        "Short-term assets divided by short-term liabilities — can the company pay bills due within a year?",
        "Green at 1.5+, red below 1. Below 1 isn't always trouble for companies with steady cash inflows (e.g. big retailers).",
    ),
    "Interest coverage": (
        "Operating profit (EBIT) divided by interest expense — how many times profit covers the interest bill.",
        "Green at 5x+, red below 2x. Under about 1.5x means profits barely cover interest.",
    ),
    "Total debt": (
        "All borrowings, including lease obligations.",
        "Judge it against cash and earnings, not in isolation — large companies can carry large debt safely.",
    ),
    "Cash & short-term investments": (
        "Cash plus investments that can be turned into cash quickly.",
        "A big cash pile is a cushion and funds buybacks, dividends and acquisitions.",
    ),
    "Net debt (last FY)": (
        "Total debt minus cash. Negative means the company holds more cash than debt.",
        "Negative ('net cash') is the safest position. Compare with EBITDA: net debt over about 3x EBITDA is heavy.",
    ),
    "Operating cash flow": (
        "Cash the core business generated, after working-capital changes but before capital spending.",
        "Should be positive and, over time, close to or above net income. Green when positive.",
    ),
    "Capital expenditure (last FY)": (
        "Cash spent on equipment, buildings and technology, shown as a negative (cash out).",
        "Large capex reduces free cash flow now but can fund future growth. Watch whether it jumps versus revenue.",
    ),
    "Free cash flow": (
        "Operating cash flow minus capital spending — cash left over for dividends, buybacks, debt repayment or growth.",
        "Green when positive. Many investors consider it the truest measure of earnings power.",
    ),
    "FCF conversion (FCF / net income)": (
        "How much of reported profit turned into free cash.",
        "Green at 80%+, red below 50%. Persistently low conversion means profits aren't turning into cash — investigate why.",
    ),
    "Dividend yield": (
        "Annual dividend per share divided by the share price.",
        "Higher pays you more now, but a very high yield can signal the market doubts it can be kept. Many growth companies pay none.",
    ),
    "Payout ratio": (
        "The share of earnings paid out as dividends.",
        "Green at 60% or below (room to keep paying), red above 100% (paying out more than it earns).",
    ),
    "Buybacks (last FY)": (
        "Cash spent repurchasing the company's own shares.",
        "Returns cash to owners and shrinks the share count, raising EPS. Less valuable if it merely offsets share-based pay.",
    ),
    "Share count change (years shown)": (
        "Change in diluted share count over the fiscal years shown.",
        "Green when shares shrink by 1%+ (buybacks outpace new issuance), red when they grow 2%+ (existing owners are diluted).",
    ),
    "Mean price target": (
        "The average price analysts expect in about a year.",
        "A consensus guess, not a promise. Targets cluster near the current price and are revised after price moves.",
    ),
    "Upside to target": (
        "How far the mean target sits above (or below) the current price.",
        "Green at +10% or more, red at -10% or less. Check how many analysts cover the stock before relying on it.",
    ),
    "Recommendation": (
        "The consensus rating (e.g. Buy, Hold) from Yahoo's analyst data.",
        "Most ratings are Buy or Hold, so a 'Hold' is often the cautious end of the scale. Treat it as sentiment, not advice.",
    ),
}

READING_ORDER = [
    ("Growth", "Is the business expanding? Check revenue and profit growth over several years, not just the last one."),
    ("Profitability", "Does the growth make money? Margins and returns show quality; compare them with peers."),
    ("Financial health", "Can it handle a rough patch? Look at debt versus cash and interest coverage."),
    ("Cash flow", "Is profit turning into cash? Free cash flow should be positive and track net income."),
    ("Valuation", "Only now ask: is the price fair for all of the above? Compare multiples across the tickers you loaded."),
]

GUIDE_CAVEATS = [
    "Colours are rule-of-thumb cues (green = looks healthy, red = worth a closer look), not recommendations. What's "
    "normal depends heavily on the industry — banks, utilities, software and retailers all look different.",
    "Compare companies in the same industry and look at several years of history; one number in isolation rarely means much.",
    "Some rows use Yahoo's trailing-twelve-month data and some use the latest fiscal year, so they can cover slightly "
    "different periods (the 'Latest fiscal year' row shows when the annual data ends).",
    "A dash (—) means Yahoo didn't report that figure, which is common for newer or foreign-listed companies.",
    "This is educational information, not investment advice.",
]
