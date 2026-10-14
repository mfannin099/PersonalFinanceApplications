import pandas as pd
import pytest

from CreditCard.categorize import categorize_transactions, save_override, uncategorized_merchants


@pytest.fixture
def overrides_path(tmp_path):
    return tmp_path / "merchant_overrides.json"


def test_keyword_rule_match():
    df = pd.DataFrame({"Description": ["Starbucks Coffee"], "Amount": [5.0]})
    result = categorize_transactions(df, overrides={})
    assert result.iloc[0]["Category"] == "Food & Drink"


def test_unmatched_merchant_is_uncategorized():
    df = pd.DataFrame({"Description": ["Some Unknown Merchant"], "Amount": [5.0]})
    result = categorize_transactions(df, overrides={})
    assert result.iloc[0]["Category"] == "Uncategorized"


def test_override_takes_priority_over_keyword():
    df = pd.DataFrame({"Description": ["Starbucks Coffee"], "Amount": [5.0]})
    result = categorize_transactions(df, overrides={"Starbucks Coffee": "Personal"})
    assert result.iloc[0]["Category"] == "Personal"


def test_falls_back_to_existing_category_column():
    df = pd.DataFrame(
        {"Description": ["Some Unknown Merchant"], "Amount": [5.0], "Category": ["Bank Provided Category"]}
    )
    result = categorize_transactions(df, overrides={})
    assert result.iloc[0]["Category"] == "Bank Provided Category"


def test_uncategorized_merchants_lists_unmatched():
    df = pd.DataFrame({"Description": ["Known Store", "Unknown Store"], "Amount": [1.0, 2.0]})
    result = categorize_transactions(df, overrides={"Known Store": "Shopping"})
    assert uncategorized_merchants(result) == ["Unknown Store"]


def test_save_override_persists_to_disk(overrides_path):
    save_override("My Merchant", "Groceries", path=overrides_path)
    assert overrides_path.exists()

    df = pd.DataFrame({"Description": ["My Merchant"], "Amount": [1.0]})
    from CreditCard.categorize import load_overrides

    result = categorize_transactions(df, overrides=load_overrides(overrides_path))
    assert result.iloc[0]["Category"] == "Groceries"
