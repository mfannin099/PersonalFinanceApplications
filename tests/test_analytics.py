import pandas as pd

from CreditCard.analytics import (
    category_zscores,
    share_of_wallet,
    spend_by_period_and_category,
    spend_by_weekday,
)


def _df(rows):
    df = pd.DataFrame(rows)
    df["Transaction Date"] = pd.to_datetime(df["Transaction Date"])
    return df


def test_spend_by_period_and_category_buckets_long_tail_into_other():
    df = _df(
        [
            {"Transaction Date": "2024-01-01", "Category": "A", "Amount": 100},
            {"Transaction Date": "2024-01-02", "Category": "B", "Amount": 1},
        ]
    )
    result = spend_by_period_and_category(df, freq="D", top_n=1)
    assert set(result["Category"]) == {"A", "Other"}


def test_spend_by_period_and_category_sums_within_period():
    df = _df(
        [
            {"Transaction Date": "2024-01-01", "Category": "A", "Amount": 10},
            {"Transaction Date": "2024-01-01", "Category": "A", "Amount": 5},
        ]
    )
    result = spend_by_period_and_category(df, freq="D", top_n=8)
    assert result.loc[result["Category"] == "A", "Amount"].iloc[0] == 15


def test_category_zscores_flags_relative_to_own_category():
    # Category A has a huge outlier; Category B's amounts are all similar.
    df = pd.DataFrame(
        {
            "Category": ["A", "A", "A", "B", "B", "B"],
            "Amount": [10, 12, 11, 500, 510, 490],
        }
    )
    result = category_zscores(df, z_threshold=1.5)
    # B's transactions are all close to B's own mean -> never flagged despite larger $.
    assert not result.loc[result["Category"] == "B", "is_one_off_large"].any()


def test_category_zscores_handles_single_transaction_category():
    df = pd.DataFrame({"Category": ["A", "A", "C"], "Amount": [10, 12, 1000]})
    result = category_zscores(df, z_threshold=1.0)
    # Category C has only one transaction -> std is NaN -> never flagged, no crash.
    assert result.loc[result["Category"] == "C", "is_one_off_large"].iloc[0] == False  # noqa: E712


def test_spend_by_weekday_orders_monday_through_sunday():
    df = _df(
        [
            {"Transaction Date": "2024-01-07", "Amount": 5},  # Sunday
            {"Transaction Date": "2024-01-01", "Amount": 10},  # Monday
        ]
    )
    result = spend_by_weekday(df)
    assert result["Weekday"].tolist() == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    ]
    assert result.loc[result["Weekday"] == "Monday", "Amount"].iloc[0] == 10
    assert result.loc[result["Weekday"] == "Sunday", "Amount"].iloc[0] == 5


def test_spend_by_weekday_fills_missing_days_with_zero():
    df = _df([{"Transaction Date": "2024-01-01", "Amount": 10}])  # only Monday
    result = spend_by_weekday(df)
    assert result.loc[result["Weekday"] == "Tuesday", "Amount"].iloc[0] == 0


def test_share_of_wallet_percentages_sum_to_100():
    df = pd.DataFrame({"Category": ["A", "B"], "Amount": [25, 75]})
    result = share_of_wallet(df)
    assert result.loc[result["Category"] == "A", "pct_of_total"].iloc[0] == 25.0
    assert result.loc[result["Category"] == "B", "pct_of_total"].iloc[0] == 75.0


def test_share_of_wallet_handles_zero_total():
    df = pd.DataFrame({"Category": [], "Amount": []})
    result = share_of_wallet(df)
    assert result.empty
