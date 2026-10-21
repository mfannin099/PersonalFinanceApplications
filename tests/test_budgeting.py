import pandas as pd
import pytest

from Budget.budgeting import (
    BUDGET_CATEGORIES,
    NON_CARD_CATEGORIES,
    budget_summary,
    budget_vs_actual,
    suggested_allocations,
)

INCOME = 5000.0


def _amount(df: pd.DataFrame, category: str) -> float:
    return df.loc[df["Category"] == category, "Budgeted Amount"].iloc[0]


def test_50_30_20_bucket_sums():
    df = suggested_allocations("50/30/20", INCOME)
    needs = df[df["Category"].isin(["Bills & Utilities", "Groceries", "Gas", "Health & Wellness",
                                     "Automotive", "Fees & Adjustments", "Rent/Mortgage",
                                     "Insurance", "Childcare"])]["Budgeted Amount"].sum()
    wants = df[df["Category"].isin(["Food & Drink", "Entertainment", "Shopping", "Travel", "Personal"])][
        "Budgeted Amount"
    ].sum()
    savings = df[df["Category"].isin(["Savings", "Debt Payments"])]["Budgeted Amount"].sum()

    assert needs == pytest.approx(0.50 * INCOME, abs=0.1)
    assert wants == pytest.approx(0.30 * INCOME, abs=0.1)
    assert savings == pytest.approx(0.20 * INCOME, abs=0.1)
    assert _amount(df, "Money out") == 0
    assert _amount(df, "Uncategorized") == 0


def test_zero_based_even_split_sums_to_income():
    df = suggested_allocations("Zero-Based", INCOME)
    assert df["Budgeted Amount"].sum() == pytest.approx(INCOME, abs=0.1)
    assert df["Budgeted Amount"].nunique() == 1


def test_pay_yourself_first_savings_default_rate():
    df = suggested_allocations("Pay Yourself First", INCOME)
    assert _amount(df, "Savings") == pytest.approx(0.20 * INCOME, abs=0.01)
    assert _amount(df, "Money out") == 0
    assert _amount(df, "Uncategorized") == 0


def test_pay_yourself_first_custom_rate():
    df = suggested_allocations("Pay Yourself First", INCOME, savings_rate=0.35)
    assert _amount(df, "Savings") == pytest.approx(0.35 * INCOME, abs=0.01)


def test_envelope_matches_50_30_20():
    envelope = suggested_allocations("Envelope", INCOME)
    standard = suggested_allocations("50/30/20", INCOME)
    pd.testing.assert_frame_equal(envelope, standard)


def test_unrecognized_method_raises():
    with pytest.raises(ValueError):
        suggested_allocations("Not A Method", INCOME)


def test_budget_vs_actual_per_category_sums():
    allocations = pd.DataFrame({"Category": ["Groceries", "Gas"], "Budgeted Amount": [400.0, 100.0]})
    transactions = pd.DataFrame({"Category": ["Groceries", "Groceries", "Gas"], "Amount": [50.0, 60.0, 40.0]})

    result = budget_vs_actual(allocations, transactions)

    groceries = result[result["Category"] == "Groceries"].iloc[0]
    assert groceries["Actual Amount"] == 110.0
    assert groceries["Budgeted Amount"] == 400.0
    assert groceries["Variance"] == pytest.approx(290.0)
    assert groceries["Status"] == "Under"

    assert set(result["Category"]) == set(BUDGET_CATEGORIES)


def test_non_card_categories_always_zero_actual():
    allocations = pd.DataFrame({"Category": NON_CARD_CATEGORIES, "Budgeted Amount": [1000.0] * len(NON_CARD_CATEGORIES)})
    transactions = pd.DataFrame({"Category": ["Rent/Mortgage"], "Amount": [999.0]})

    result = budget_vs_actual(allocations, transactions)

    for category in NON_CARD_CATEGORIES:
        assert result.loc[result["Category"] == category, "Actual Amount"].iloc[0] == 0.0


def test_budget_vs_actual_status_thresholds():
    allocations = pd.DataFrame(
        {"Category": ["Groceries", "Gas", "Shopping"], "Budgeted Amount": [100.0, 100.0, 100.0]}
    )
    transactions = pd.DataFrame(
        {"Category": ["Groceries", "Gas", "Shopping"], "Amount": [50.0, 90.0, 150.0]}
    )

    result = budget_vs_actual(allocations, transactions).set_index("Category")

    assert result.loc["Groceries", "Status"] == "Under"
    assert result.loc["Gas", "Status"] == "Near"
    assert result.loc["Shopping", "Status"] == "Over"


def test_budget_vs_actual_no_budget_status_avoids_division_by_zero():
    allocations = pd.DataFrame({"Category": ["Groceries"], "Budgeted Amount": [0.0]})
    transactions = pd.DataFrame({"Category": ["Groceries"], "Amount": [25.0]})

    result = budget_vs_actual(allocations, transactions)
    row = result[result["Category"] == "Groceries"].iloc[0]

    assert row["Status"] == "No Budget"
    assert row["Pct Used"] is None


def test_budget_vs_actual_handles_no_transactions():
    allocations = pd.DataFrame({"Category": ["Groceries"], "Budgeted Amount": [100.0]})
    result = budget_vs_actual(allocations, None)
    assert result.loc[result["Category"] == "Groceries", "Actual Amount"].iloc[0] == 0.0


def test_budget_summary_unallocated_cases():
    fully_allocated = pd.DataFrame({"Category": ["Groceries"], "Budgeted Amount": [1000.0]})
    under_allocated = pd.DataFrame({"Category": ["Groceries"], "Budgeted Amount": [600.0]})
    over_allocated = pd.DataFrame({"Category": ["Groceries"], "Budgeted Amount": [1200.0]})

    assert budget_summary(1000.0, fully_allocated)["unallocated"] == 0.0
    assert budget_summary(1000.0, under_allocated)["unallocated"] == pytest.approx(400.0)
    assert budget_summary(1000.0, over_allocated)["unallocated"] == pytest.approx(-200.0)
