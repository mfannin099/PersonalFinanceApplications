import pandas as pd

from CreditCard.analytics import (
    category_zscores,
    daily_spending_stats,
    share_of_wallet,
    spend_by_period_and_category,
    spend_by_weekday,
    transactions_for_period,
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
    assert result.loc[result["Weekday"] == "Monday", "Total"].iloc[0] == 10
    assert result.loc[result["Weekday"] == "Sunday", "Total"].iloc[0] == 5


def test_spend_by_weekday_fills_missing_days_with_zero():
    df = _df([{"Transaction Date": "2024-01-01", "Amount": 10}])  # only Monday
    result = spend_by_weekday(df)
    assert result.loc[result["Weekday"] == "Tuesday", "Total"].iloc[0] == 0
    assert result.loc[result["Weekday"] == "Tuesday", "Average"].iloc[0] == 0


def test_spend_by_weekday_average_is_mean_of_daily_totals_not_transactions():
    # Two Mondays: $100 (one purchase) and $20 (two $10 purchases) -> avg daily total is $60.
    df = _df(
        [
            {"Transaction Date": "2024-01-01", "Amount": 100},
            {"Transaction Date": "2024-01-08", "Amount": 10},
            {"Transaction Date": "2024-01-08", "Amount": 10},
        ]
    )
    result = spend_by_weekday(df)
    assert result.loc[result["Weekday"] == "Monday", "Total"].iloc[0] == 120
    assert result.loc[result["Weekday"] == "Monday", "Average"].iloc[0] == 60


def test_share_of_wallet_percentages_sum_to_100():
    df = pd.DataFrame({"Category": ["A", "B"], "Amount": [25, 75]})
    result = share_of_wallet(df)
    assert result.loc[result["Category"] == "A", "pct_of_total"].iloc[0] == 25.0
    assert result.loc[result["Category"] == "B", "pct_of_total"].iloc[0] == 75.0


def test_share_of_wallet_handles_zero_total():
    df = pd.DataFrame({"Category": [], "Amount": []})
    result = share_of_wallet(df)
    assert result.empty


def test_transactions_for_period_daily_matches_exact_day():
    df = _df(
        [
            {"Transaction Date": "2024-01-05", "Amount": 10},
            {"Transaction Date": "2024-01-06", "Amount": 20},
        ]
    )
    result = transactions_for_period(df, pd.Timestamp("2024-01-05"), freq="D")
    assert result["Amount"].tolist() == [10]


def test_transactions_for_period_weekly_matches_resample_bucket():
    df = _df(
        [
            {"Transaction Date": "2024-01-01", "Amount": 10},  # Monday
            {"Transaction Date": "2024-01-07", "Amount": 20},  # Sunday, same week
            {"Transaction Date": "2024-01-08", "Amount": 30},  # next Monday, different week
        ]
    )
    weekly = df.set_index("Transaction Date").resample("W")["Amount"].sum().reset_index()
    first_week_label = weekly["Transaction Date"].iloc[0]

    result = transactions_for_period(df, first_week_label, freq="W")
    assert sorted(result["Amount"].tolist()) == [10, 20]


def test_transactions_for_period_monthly_matches_resample_bucket():
    df = _df(
        [
            {"Transaction Date": "2024-01-15", "Amount": 10},
            {"Transaction Date": "2024-01-31", "Amount": 20},
            {"Transaction Date": "2024-02-01", "Amount": 30},
        ]
    )
    monthly = df.set_index("Transaction Date").resample("ME")["Amount"].sum().reset_index()
    january_label = monthly["Transaction Date"].iloc[0]

    result = transactions_for_period(df, january_label, freq="ME")
    assert sorted(result["Amount"].tolist()) == [10, 20]


def test_daily_spending_stats_includes_zero_spend_days_in_range():
    # Jan 1 ($30, 2 txns), Jan 2 (nothing), Jan 3 ($10, 1 txn) -> 3-day range.
    df = _df(
        [
            {"Transaction Date": "2024-01-01", "Amount": 10},
            {"Transaction Date": "2024-01-01", "Amount": 20},
            {"Transaction Date": "2024-01-03", "Amount": 10},
        ]
    )
    stats = daily_spending_stats(df)
    # Daily totals across the full range: [30, 0, 10] -> median 10.
    assert stats["median_daily_spend"] == 10
    # Daily transaction counts across the full range: [2, 0, 1] -> mean 1.0.
    assert stats["avg_daily_transactions"] == 1.0


def test_daily_spending_stats_single_day():
    df = _df([{"Transaction Date": "2024-01-01", "Amount": 50}])
    stats = daily_spending_stats(df)
    assert stats["median_daily_spend"] == 50
    assert stats["avg_daily_transactions"] == 1.0


def test_daily_spending_stats_empty_df():
    df = pd.DataFrame({"Transaction Date": pd.to_datetime([]), "Amount": []})
    stats = daily_spending_stats(df)
    assert stats == {"median_daily_spend": 0.0, "avg_daily_transactions": 0.0}
