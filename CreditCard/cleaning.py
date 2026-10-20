"""Cleaning/normalization of a raw credit card statement export."""

import pandas as pd

REQUIRED_COLUMNS = ["Transaction Date", "Description", "Amount"]


def clean_statement(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw statement export into a consistent shape.

    Keeps only sale transactions (drops payments/credits when a ``Type``
    column is present), parses dates, takes the absolute value of amounts,
    and strips merchant descriptions down to letters/spaces so that near
    -identical merchant strings (store numbers, trailing codes) collapse
    into one entry.
    """
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Statement is missing required column(s): {', '.join(missing)}")

    df = df.copy()

    if "Type" in df.columns:
        df = df[df["Type"] == "Sale"].copy()

    df["Transaction Date"] = pd.to_datetime(df["Transaction Date"], errors="coerce")
    df = df.dropna(subset=["Transaction Date"])

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").abs()
    df = df.dropna(subset=["Amount"])

    df["Description"] = (
        df["Description"]
        .astype(str)
        .str.replace(r"[^A-Za-z\s]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    df["Was Venmoed"] = False
    df["Split Count"] = df.get("Split Count", 1)

    df = df.drop(columns=["Source File", "Memo", "Type"], errors="ignore")

    return df.reset_index(drop=True)
