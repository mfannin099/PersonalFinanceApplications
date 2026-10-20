"""Pure analytics helpers for the Credit Card dashboard (no Streamlit here)."""

import pandas as pd

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def spend_by_period_and_category(df: pd.DataFrame, freq: str, top_n: int = 8) -> pd.DataFrame:
    """Total spend per resample period, broken out by category.

    Categories outside the top ``top_n`` by total spend are collapsed into
    "Other" so multi-category charts stay readable.
    """
    top_categories = (
        df.groupby("Category")["Amount"].sum().sort_values(ascending=False).head(top_n).index
    )
    bucketed = df.copy()
    bucketed["Category"] = bucketed["Category"].where(bucketed["Category"].isin(top_categories), "Other")

    return (
        bucketed.set_index("Transaction Date")
        .groupby("Category")
        .resample(freq)["Amount"]
        .sum()
        .reset_index()
    )


def category_zscores(df: pd.DataFrame, z_threshold: float) -> pd.DataFrame:
    """Flag large one-off purchases relative to their own category's mean/std.

    Categories with fewer than 2 transactions have an undefined std dev;
    those rows get ``z_score = NaN`` and are never flagged.
    """
    df = df.copy()
    cat_mean = df.groupby("Category")["Amount"].transform("mean")
    cat_std = df.groupby("Category")["Amount"].transform("std")

    df["z_score"] = (df["Amount"] - cat_mean) / cat_std
    df["is_one_off_large"] = df["z_score"] > z_threshold
    df["is_one_off_large"] = df["is_one_off_large"].fillna(False)
    return df


def spend_by_weekday(df: pd.DataFrame) -> pd.DataFrame:
    """Total and average-per-day spend by day of week, Monday through Sunday.

    "Average" is the mean of each individual day's total (e.g. the average
    of all Saturdays' totals), not the mean transaction amount — that way a
    weekday with many small purchases isn't diluted relative to one with
    few large ones.
    """
    daily_totals = (
        df.assign(Date=df["Transaction Date"].dt.date)
        .groupby("Date")["Amount"]
        .sum()
        .reset_index()
    )
    daily_totals["Weekday"] = pd.to_datetime(daily_totals["Date"]).dt.day_name()

    weekday_spend = (
        daily_totals.groupby("Weekday")["Amount"]
        .agg(Total="sum", Average="mean")
        .reindex(WEEKDAY_ORDER, fill_value=0)
        .reset_index()
    )
    return weekday_spend


def share_of_wallet(df: pd.DataFrame) -> pd.DataFrame:
    """Category totals plus each category's percentage of total spend."""
    category_totals = df.groupby("Category")["Amount"].sum().reset_index()
    total = category_totals["Amount"].sum()
    category_totals["pct_of_total"] = (category_totals["Amount"] / total * 100) if total else 0.0
    return category_totals.sort_values("Amount", ascending=False)
