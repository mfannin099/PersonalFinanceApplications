"""Pure, Dash-free budgeting logic: presets, allocations, and budget-vs-actual.

Parallel to ``CreditCard/analytics.py`` — no Dash/UI imports here, just
pandas, so this module is unit-tested directly in ``tests/test_budgeting.py``.
"""

import pandas as pd

from CreditCard.categorize import CATEGORIES as CARD_CATEGORIES
from CreditCard.categorize import NEEDS_WANTS_MAP

# Budget line items that never appear on a credit card statement — paid by
# other means (ACH, check, auto-draft), so "actual" spend for these is
# always $0 in budget_vs_actual; they exist purely as budget targets.
# ("Insurance" used to be here too, but it's now folded into the
# "Bills & Utilities" line item below rather than budgeted separately.)
NON_CARD_CATEGORIES = ["Rent/Mortgage", "Savings", "Debt Payments"]

# CreditCard categories that aren't budget line items here: "Money out" is
# a transfer, not spending, and "Fees & Adjustments" isn't something a user
# plans ahead for. Transactions categorized either way by CreditCard are
# simply excluded from every Budget view (not redistributed elsewhere).
_EXCLUDED_CARD_CATEGORIES = ["Money out", "Fees & Adjustments"]

# CreditCard's "Shopping" reads as a budget line item under the clearer
# name "Misc Shopping", and "Bills & Utilities" is relabeled to show it now
# also covers the (removed, non-card) "Insurance" budget. These are the
# deliberate exceptions to reusing CreditCard's category names verbatim —
# everywhere a transaction's raw category needs to line up with a Budget
# category, it's passed through this map first (see relabel_for_budget).
_CARD_CATEGORY_RELABELS = {
    "Shopping": "Misc Shopping",
    "Bills & Utilities": "Bills & Utilities (+ Insurance)",
}


def _budget_label(category: str) -> str:
    return _CARD_CATEGORY_RELABELS.get(category, category)


def relabel_for_budget(transactions: pd.DataFrame) -> pd.DataFrame:
    """Rename a categorized statement's ``Category`` values to match Budget's labels.

    Only the entries in ``_CARD_CATEGORY_RELABELS`` change; everything else
    passes through unchanged (including the excluded categories, which
    simply won't match anything in ``BUDGET_CATEGORIES`` downstream).
    """
    if transactions.empty:
        return transactions
    return transactions.assign(Category=transactions["Category"].replace(_CARD_CATEGORY_RELABELS))


_ELIGIBLE_CARD_CATEGORIES = [c for c in CARD_CATEGORIES if c not in _EXCLUDED_CARD_CATEGORIES]

# The full set of budget line items: every CreditCard category except the
# excluded ones above (relabeled where applicable) plus the non-card
# categories above.
BUDGET_CATEGORIES = [_budget_label(c) for c in _ELIGIBLE_CARD_CATEGORIES] + NON_CARD_CATEGORIES

# A budget is a monthly plan, so an uploaded statement is compared against
# only its most recent ~month, not its full history.
RECENT_WINDOW_DAYS = 30


def filter_to_recent_window(transactions: pd.DataFrame, days: int = RECENT_WINDOW_DAYS) -> pd.DataFrame:
    """Keep only the last ``days`` days of transactions, inclusive.

    Anchored to the statement's own latest ``Transaction Date``, not
    wall-clock time — consistent with the "new merchant" window in
    ``CreditCard/app.py`` — so uploading an old statement still compares
    against its own most recent activity rather than today's date.
    """
    if transactions.empty:
        return transactions
    transactions = transactions.copy()
    transactions["Transaction Date"] = pd.to_datetime(transactions["Transaction Date"])
    cutoff = transactions["Transaction Date"].max() - pd.Timedelta(days=days - 1)
    return transactions[transactions["Transaction Date"] >= cutoff].reset_index(drop=True)

PRESETS: dict[str, str] = {
    "50/30/20": "50% of income to Needs, 30% to Wants, 20% to Savings & debt paydown.",
    "Zero-Based": "Every dollar is assigned a category; nothing is left unallocated.",
    "Pay Yourself First": "Savings comes off the top automatically; the remainder is budgeted loosely.",
}

# Card categories classified "Other" by NEEDS_WANTS_MAP (transfers/unknowns)
# — budgeting for these is meaningless, so they're always seeded $0.
# (Derived from the eligible/relabeled list, so excluded categories never
# show up here even though NEEDS_WANTS_MAP itself still has them.)
OTHER_CARD = [_budget_label(c) for c in _ELIGIBLE_CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Other"]
NEEDS_CARD = [_budget_label(c) for c in _ELIGIBLE_CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Essential"]
WANTS_CARD = [_budget_label(c) for c in _ELIGIBLE_CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Discretionary"]

_NEEDS_BUCKET = NEEDS_CARD + ["Rent/Mortgage"]
_WANTS_BUCKET = WANTS_CARD
_SAVINGS_BUCKET = ["Savings", "Debt Payments"]

# Relative weight of each category within whatever bucket it's split
# across, loosely based on typical U.S. household expense shares (e.g.
# housing dominates Needs; dining/"Misc Shopping" dominate Wants). An even
# split would give "Rent/Mortgage" the same suggested amount as "Gas" —
# these weights avoid that. Units are arbitrary and only meaningful
# relative to other weights in the same split; "Uncategorized" is always 0
# since budgeting for unknown spend is meaningless.
CATEGORY_WEIGHTS: dict[str, float] = {
    "Rent/Mortgage": 40,
    "Bills & Utilities (+ Insurance)": 15,  # was Bills & Utilities (8) + Insurance (7)
    "Groceries": 14,
    "Gas": 6,
    "Health & Wellness": 7,
    "Automotive": 5,
    "Food & Drink": 12,
    "Entertainment": 5,
    "Misc Shopping": 10,
    "Travel": 4,
    "Personal": 4,
    "Savings": 12,
    "Debt Payments": 8,
    "Uncategorized": 0,
}


def _zero_allocations() -> dict[str, float]:
    return {category: 0.0 for category in BUDGET_CATEGORIES}


def _weighted_split(allocations: dict[str, float], categories: list[str], amount: float) -> None:
    if not categories:
        return
    total_weight = sum(CATEGORY_WEIGHTS.get(c, 0) for c in categories)
    if total_weight == 0:
        return
    for category in categories:
        share = amount * CATEGORY_WEIGHTS.get(category, 0) / total_weight
        allocations[category] = round(share, 2)


def _suggest_50_30_20(income: float) -> dict[str, float]:
    allocations = _zero_allocations()
    _weighted_split(allocations, _NEEDS_BUCKET, 0.50 * income)
    _weighted_split(allocations, _WANTS_BUCKET, 0.30 * income)
    _weighted_split(allocations, _SAVINGS_BUCKET, 0.20 * income)
    return allocations


def _suggest_zero_based(income: float) -> dict[str, float]:
    allocations = _zero_allocations()
    _weighted_split(allocations, BUDGET_CATEGORIES, income)
    return allocations


def _suggest_pay_yourself_first(income: float, savings_rate: float) -> dict[str, float]:
    allocations = _zero_allocations()
    savings_amount = round(savings_rate * income, 2)
    allocations["Savings"] = savings_amount
    remainder_categories = [c for c in BUDGET_CATEGORIES if c not in ("Savings", *OTHER_CARD)]
    _weighted_split(allocations, remainder_categories, income - savings_amount)
    return allocations


def suggested_allocations(method: str, income: float, *, savings_rate: float = 0.20) -> pd.DataFrame:
    """Starting-point $ allocation per category for a named budgeting method.

    Within any bucket (or across everything, for ``Zero-Based``), amounts
    are split by ``CATEGORY_WEIGHTS`` (not evenly) so e.g. Rent/Mortgage is
    suggested a realistic multiple of Gas rather than an identical share.
    ``Zero-Based`` differs from ``50/30/20`` by skipping the 50/30/20
    tiering and weighting every category against every other directly, so
    e.g. ``Savings`` competes on the same footing as spending categories
    instead of being guaranteed a 20% floor. These are suggestions to seed
    the editable allocations table, not a final answer — the user is
    expected to adjust them. Raises ``ValueError`` for an unrecognized
    ``method``.
    """
    if method == "50/30/20":
        allocations = _suggest_50_30_20(income)
    elif method == "Zero-Based":
        allocations = _suggest_zero_based(income)
    elif method == "Pay Yourself First":
        allocations = _suggest_pay_yourself_first(income, savings_rate)
    else:
        raise ValueError(f"Unrecognized budgeting method: {method!r}")

    return pd.DataFrame(
        {"Category": BUDGET_CATEGORIES, "Budgeted Amount": [allocations[c] for c in BUDGET_CATEGORIES]}
    )


def _status(budgeted: float, actual: float) -> str:
    if budgeted == 0:
        return "No Budget" if actual > 0 else "Under"
    pct = actual / budgeted * 100
    if pct > 100:
        return "Over"
    if pct >= 80:
        return "Near"
    return "Under"


def budget_vs_actual(allocations: pd.DataFrame, transactions: pd.DataFrame | None) -> pd.DataFrame:
    """Per-category budgeted $ vs. actual $, with variance, % used, and status.

    ``allocations`` needs ``Category``/``Budgeted Amount`` columns (any
    subset/order of ``BUDGET_CATEGORIES`` — missing categories default to
    $0 budgeted). ``transactions`` is a categorized statement (``Category``/
    ``Amount`` columns), or ``None``/empty if nothing has been uploaded yet.
    Every row in ``NON_CARD_CATEGORIES`` (3 categories) always gets
    ``Actual Amount == 0``, since those never appear on a card statement.
    Transactions
    categorized "Money out" or "Fees & Adjustments" are excluded entirely —
    they don't appear as $0, they simply aren't counted anywhere.
    """
    budgeted_by_category = dict(zip(allocations["Category"], allocations["Budgeted Amount"]))

    if transactions is not None and not transactions.empty:
        actual_by_category = relabel_for_budget(transactions).groupby("Category")["Amount"].sum().to_dict()
    else:
        actual_by_category = {}

    rows = []
    for category in BUDGET_CATEGORIES:
        budgeted = float(budgeted_by_category.get(category, 0.0))
        actual = 0.0 if category in NON_CARD_CATEGORIES else float(actual_by_category.get(category, 0.0))
        variance = budgeted - actual
        pct_used = None if budgeted == 0 else round(actual / budgeted * 100, 1)
        rows.append(
            {
                "Category": category,
                "Budgeted Amount": budgeted,
                "Actual Amount": actual,
                "Variance": variance,
                "Pct Used": pct_used,
                "Status": _status(budgeted, actual),
            }
        )
    return pd.DataFrame(rows)


def budget_summary(income: float, allocations: pd.DataFrame) -> dict:
    """Total income, total budgeted, and how much of income is unallocated."""
    total_budgeted = float(allocations["Budgeted Amount"].sum())
    return {
        "total_income": income,
        "total_budgeted": total_budgeted,
        "unallocated": round(income - total_budgeted, 2),
    }
