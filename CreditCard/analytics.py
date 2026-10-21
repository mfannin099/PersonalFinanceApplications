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
    """Spend and transaction-count stats by day of week, Monday through Sunday.

    Built over every calendar day in the data's date range (including
    zero-spend days), consistent with ``daily_spending_stats``. "Average"
    and "Median" are the mean/median of each individual day's *total*
    (e.g. across all Saturdays), not the mean transaction amount — so a
    weekday with many small purchases isn't diluted relative to one with
    few large ones.
    """
    empty_columns = ["Weekday", "Total", "Average", "Median", "Avg Transaction Count"]
    if df.empty:
        return pd.DataFrame({col: (WEEKDAY_ORDER if col == "Weekday" else 0.0) for col in empty_columns})

    start = df["Transaction Date"].min().normalize()
    end = df["Transaction Date"].max().normalize()
    full_range = pd.date_range(start, end, freq="D")

    daily = (
        df.assign(Date=df["Transaction Date"].dt.normalize())
        .groupby("Date")
        .agg(Total=("Amount", "sum"), Transactions=("Amount", "count"))
        .reindex(full_range, fill_value=0)
    )
    daily["Weekday"] = daily.index.day_name()

    weekday_spend = (
        daily.groupby("Weekday")
        .agg(
            Total=("Total", "sum"),
            Average=("Total", "mean"),
            Median=("Total", "median"),
            **{"Avg Transaction Count": ("Transactions", "mean")},
        )
        .reindex(WEEKDAY_ORDER, fill_value=0)
        .reset_index()
    )
    return weekday_spend


def daily_spending_stats(df: pd.DataFrame) -> dict:
    """Median $ spent per day and average # of transactions per day.

    Computed over every calendar day in the data's date range (inclusive),
    including days with zero transactions/spend — not just days that had
    at least one purchase.
    """
    if df.empty:
        return {"median_daily_spend": 0.0, "avg_daily_transactions": 0.0}

    start = df["Transaction Date"].min().normalize()
    end = df["Transaction Date"].max().normalize()
    full_range = pd.date_range(start, end, freq="D")

    daily = (
        df.assign(Date=df["Transaction Date"].dt.normalize())
        .groupby("Date")
        .agg(Total=("Amount", "sum"), Transactions=("Amount", "count"))
        .reindex(full_range, fill_value=0)
    )

    return {
        "median_daily_spend": daily["Total"].median(),
        "avg_daily_transactions": daily["Transactions"].mean(),
    }


def transactions_for_period(df: pd.DataFrame, period_label, freq: str) -> pd.DataFrame:
    """Return the transactions falling in the same resample bucket as ``period_label``.

    ``period_label`` is one of the "Transaction Date" values produced by
    ``df.resample(freq)[...]`` — i.e. a value already plotted on the x-axis
    of the Spending Over Time chart — so selecting a point on that chart can
    look up exactly the rows that fed it.
    """
    if freq == "D":
        mask = df["Transaction Date"].dt.date == pd.Timestamp(period_label).date()
    elif freq == "W":
        mask = df["Transaction Date"].dt.to_period("W") == pd.Period(period_label, freq="W")
    else:
        mask = df["Transaction Date"].dt.to_period("M") == pd.Period(period_label, freq="M")
    return df[mask]


def share_of_wallet(df: pd.DataFrame) -> pd.DataFrame:
    """Category totals plus each category's percentage of total spend."""
    category_totals = df.groupby("Category")["Amount"].sum().reset_index()
    total = category_totals["Amount"].sum()
    category_totals["pct_of_total"] = (category_totals["Amount"] / total * 100) if total else 0.0
    return category_totals.sort_values("Amount", ascending=False)
