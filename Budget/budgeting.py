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
NON_CARD_CATEGORIES = ["Rent/Mortgage", "Insurance", "Savings", "Debt Payments", "Childcare"]

# The full set of budget line items: every CreditCard category (reused
# verbatim so a categorized transaction maps into the budget with no
# translation layer) plus the non-card categories above.
BUDGET_CATEGORIES = CARD_CATEGORIES + NON_CARD_CATEGORIES

PRESETS: dict[str, str] = {
    "50/30/20": "50% of income to Needs, 30% to Wants, 20% to Savings & debt paydown.",
    "Zero-Based": "Every dollar is assigned a category; nothing is left unallocated.",
    "Envelope": "A fixed cap per category — once it's spent, stop spending in that category.",
    "Pay Yourself First": "Savings comes off the top automatically; the remainder is budgeted loosely.",
}

# Card categories classified "Other" by NEEDS_WANTS_MAP (transfers/unknowns)
# — budgeting for these is meaningless, so they're always seeded $0.
OTHER_CARD = [c for c in CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Other"]
NEEDS_CARD = [c for c in CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Essential"]
WANTS_CARD = [c for c in CARD_CATEGORIES if NEEDS_WANTS_MAP.get(c) == "Discretionary"]

_NEEDS_BUCKET = NEEDS_CARD + ["Rent/Mortgage", "Insurance", "Childcare"]
_WANTS_BUCKET = WANTS_CARD
_SAVINGS_BUCKET = ["Savings", "Debt Payments"]


def _zero_allocations() -> dict[str, float]:
    return {category: 0.0 for category in BUDGET_CATEGORIES}


def _even_split(allocations: dict[str, float], categories: list[str], amount: float) -> None:
    if not categories:
        return
    share = amount / len(categories)
    for category in categories:
        allocations[category] = round(share, 2)


def _suggest_50_30_20(income: float) -> dict[str, float]:
    allocations = _zero_allocations()
    _even_split(allocations, _NEEDS_BUCKET, 0.50 * income)
    _even_split(allocations, _WANTS_BUCKET, 0.30 * income)
    _even_split(allocations, _SAVINGS_BUCKET, 0.20 * income)
    return allocations


def _suggest_zero_based(income: float) -> dict[str, float]:
    allocations = _zero_allocations()
    _even_split(allocations, BUDGET_CATEGORIES, income)
    return allocations


def _suggest_pay_yourself_first(income: float, savings_rate: float) -> dict[str, float]:
    allocations = _zero_allocations()
    savings_amount = round(savings_rate * income, 2)
    allocations["Savings"] = savings_amount
    remainder_categories = [c for c in BUDGET_CATEGORIES if c not in ("Savings", *OTHER_CARD)]
    _even_split(allocations, remainder_categories, income - savings_amount)
    return allocations


def suggested_allocations(method: str, income: float, *, savings_rate: float = 0.20) -> pd.DataFrame:
    """Starting-point $ allocation per category for a named budgeting method.

    These are suggestions to seed the editable allocations table, not a
    final answer — the user is expected to adjust them. Raises
    ``ValueError`` for an unrecognized ``method``.
    """
    if method == "50/30/20":
        allocations = _suggest_50_30_20(income)
    elif method == "Zero-Based":
        allocations = _suggest_zero_based(income)
    elif method == "Envelope":
        # Same starting split as 50/30/20 — Envelope's defining feature is
        # the UI's hard-cap framing, not a different allocation formula.
        allocations = _suggest_50_30_20(income)
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
    Every row in ``NON_CARD_CATEGORIES`` always gets ``Actual Amount == 0``,
    since those categories never appear on a card statement.
    """
    budgeted_by_category = dict(zip(allocations["Category"], allocations["Budgeted Amount"]))

    if transactions is not None and not transactions.empty:
        actual_by_category = transactions.groupby("Category")["Amount"].sum().to_dict()
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
