import pandas as pd
import pytest

from CreditCard.cleaning import clean_statement


def test_drops_non_sale_rows():
    df = pd.DataFrame(
        {
            "Transaction Date": ["2024-01-01", "2024-01-02"],
            "Description": ["Store A", "Payment"],
            "Amount": [10.0, -500.0],
            "Type": ["Sale", "Payment"],
        }
    )
    cleaned = clean_statement(df)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["Description"] == "Store A"


def test_amount_is_absolute_value():
    df = pd.DataFrame(
        {"Transaction Date": ["2024-01-01"], "Description": ["Store A"], "Amount": [-42.5]}
    )
    cleaned = clean_statement(df)
    assert cleaned.iloc[0]["Amount"] == 42.5


def test_description_strips_non_letters():
    df = pd.DataFrame(
        {"Transaction Date": ["2024-01-01"], "Description": ["STORE #1234  EXTRA"], "Amount": [5.0]}
    )
    cleaned = clean_statement(df)
    assert cleaned.iloc[0]["Description"] == "STORE EXTRA"


def test_missing_required_column_raises():
    df = pd.DataFrame({"Transaction Date": ["2024-01-01"], "Amount": [5.0]})
    with pytest.raises(ValueError):
        clean_statement(df)


def test_invalid_rows_are_dropped_not_raised():
    df = pd.DataFrame(
        {
            "Transaction Date": ["2024-01-01", "not-a-date"],
            "Description": ["Store A", "Store B"],
            "Amount": [10.0, "oops"],
        }
    )
    cleaned = clean_statement(df)
    assert len(cleaned) == 1
