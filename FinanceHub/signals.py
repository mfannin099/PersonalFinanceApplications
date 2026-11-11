"""Money-saving signals and sample data for the Finance Hub — pure, Shiny-free.

``savings_signals`` is the CreditCard dashboard's "Money Saving Opportunities"
logic (recurring merchants, frequent small purchases, per-category large
one-offs, new merchants) lifted out of the Streamlit script so it can be
tested and shared. ``sample_statement`` makes a synthetic statement so the
hub can be tried without uploading personal data.
"""

import numpy as np
import pandas as pd

from CreditCard.analytics import category_zscores

FLAGS = ["is_recurring_candidate", "is_freq_small", "is_one_off_large", "is_new_merchant"]


def _merchant_counts(df: pd.DataFrame, flag: str) -> pd.DataFrame:
    return (
        df[df[flag]].groupby("Description").size().reset_index(name="Transaction Count")
        .sort_values("Transaction Count", ascending=False).reset_index(drop=True)
    )


def savings_signals(
    df: pd.DataFrame,
    *,
    months_seen: int = 3,
    min_count: int = 5,
    max_avg: float = 25.0,
    z_threshold: float = 3.0,
    new_days: int = 60,
) -> dict:
    """Flag merchants on four signals and combine them into cut candidates.

    Returns ``{"flagged", "recurring", "small", "one_off", "new", "candidates"}``:
    the transactions with boolean flag columns and ``cut_score``, a per-signal
    merchant summary each, and ``candidates`` — merchants flagged by at least
    two signals with their transaction count and total.

    The "new merchant" window is anchored to the statement's own latest date,
    not wall-clock time, so an old statement isn't misjudged.
    """
    empty = pd.DataFrame(columns=["Description", "Transaction Count"])
    if df.empty:
        return {"flagged": df.assign(**{f: False for f in FLAGS}, cut_score=0), "recurring": empty,
                "small": empty, "one_off": empty, "new": empty,
                "candidates": pd.DataFrame(columns=["Description", "Transactions", "Total Amount"])}

    out = df.copy()
    out["Month"] = out["Transaction Date"].dt.to_period("M")
    months_per_merchant = out.groupby("Description")["Month"].nunique()
    out["is_recurring_candidate"] = out["Description"].map(months_per_merchant >= months_seen).astype(bool)

    stats = out.groupby("Description")["Amount"].agg(["count", "mean"])
    small = stats[(stats["count"] >= min_count) & (stats["mean"] < max_avg)].index
    out["is_freq_small"] = out["Description"].isin(small)

    out = category_zscores(out, z_threshold=z_threshold)

    first_seen = out.groupby("Description")["Transaction Date"].transform("min")
    out["is_new_merchant"] = (out["Transaction Date"].max() - first_seen).dt.days <= new_days

    out["cut_score"] = out[FLAGS].sum(axis=1)
    flagged = out[out["cut_score"] >= 2]
    candidates = (
        flagged.groupby("Description")["Amount"].agg(["count", "sum"]).reset_index()
        .rename(columns={"count": "Transactions", "sum": "Total Amount"})
        .sort_values("Total Amount", ascending=False).reset_index(drop=True)
    )
    return {
        "flagged": out,
        "recurring": _merchant_counts(out, "is_recurring_candidate"),
        "small": _merchant_counts(out, "is_freq_small"),
        "one_off": _merchant_counts(out, "is_one_off_large"),
        "new": _merchant_counts(out, "is_new_merchant"),
        "candidates": candidates,
    }


_SAMPLE_MERCHANTS = [
    # (description, typical amount, purchases per month, std)
    ("Whole Foods Market", 68.0, 6, 22.0),
    ("Trader Joes", 42.0, 4, 12.0),
    ("Shell Oil", 46.0, 4, 8.0),
    ("Chipotle", 14.5, 7, 3.0),
    ("Starbucks", 6.8, 14, 1.5),
    ("Netflix", 15.49, 1, 0.0),
    ("Spotify", 11.99, 1, 0.0),
    ("Comcast Xfinity", 79.99, 1, 0.0),
    ("Amazon", 38.0, 5, 30.0),
    ("Target", 55.0, 3, 25.0),
    ("CVS Pharmacy", 21.0, 2, 9.0),
    ("Uber", 19.0, 4, 7.0),
    ("Planet Fitness", 24.99, 1, 0.0),
    ("AMC Theatres", 32.0, 1, 8.0),
]


def sample_statement(months: int = 4, seed: int = 7, end: str | None = None) -> pd.DataFrame:
    """A synthetic raw statement (``Transaction Date``/``Description``/``Amount``/``Type``).

    Deterministic for a given ``seed`` and includes one large one-off charge and
    a payment row (``Type`` of ``Payment``) that ``clean_statement`` drops, so it
    exercises the same path a real export would.
    """
    rng = np.random.default_rng(seed)
    stop = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    start = stop - pd.DateOffset(months=months)
    days = pd.date_range(start, stop, freq="D")
    rows = []
    for description, typical, per_month, std in _SAMPLE_MERCHANTS:
        n = max(1, round(per_month * months))
        if per_month == 1:  # subscriptions bill on the same day each month
            billing_day = int(rng.integers(1, 28))
            dates = [d for d in days if d.day == billing_day]
        else:
            dates = list(rng.choice(days, size=n, replace=False))
        for d in dates:
            amount = max(1.0, rng.normal(typical, std)) if std else typical
            rows.append((pd.Timestamp(d), description, round(float(amount), 2), "Sale"))
    rows.append((stop - pd.Timedelta(days=9), "Apple Store", 1349.00, "Sale"))
    rows.append((stop - pd.Timedelta(days=2), "Payment Thank You", -900.00, "Payment"))
    df = pd.DataFrame(rows, columns=["Transaction Date", "Description", "Amount", "Type"])
    df["Transaction Date"] = df["Transaction Date"].dt.strftime("%Y-%m-%d")
    return df.sort_values("Transaction Date").reset_index(drop=True)
