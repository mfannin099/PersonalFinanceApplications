"""Rule-based merchant categorization with user-editable, persisted overrides.

Categorization happens in priority order:
1. An exact-match override the user has previously saved for this merchant.
2. A keyword match against ``DEFAULT_KEYWORD_RULES``.
3. The ``Category`` column already present in the uploaded statement, if any.
4. "Uncategorized".
"""

import json
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
OVERRIDES_PATH = DATA_DIR / "merchant_overrides.json"

CATEGORIES = [
    "Bills & Utilities",
    "Food & Drink",
    "Entertainment",
    "Shopping",
    "Personal",
    "Groceries",
    "Gas",
    "Money out",
    "Health & Wellness",
    "Travel",
    "Automotive",
    "Fees & Adjustments",
    "Uncategorized",
]

# Category -> keywords matched against the cleaned (letters/spaces only) description.
DEFAULT_KEYWORD_RULES: dict[str, list[str]] = {
    "Groceries": ["kroger", "safeway", "whole foods", "trader joe", "aldi", "publix", "wegmans", "grocery"],
    "Food & Drink": [
        "starbucks", "restaurant", "doordash", "grubhub", "uber eats", "chipotle",
        "mcdonald", "coffee", "bar ", "cafe", "pizza", "taco",
    ],
    "Gas": ["shell", "chevron", "exxon", "mobil", "bp ", "gas station", "fuel", "costco gas"],
    "Bills & Utilities": [
        "electric", "water utility", "comcast", "xfinity", "verizon", "at&t", "att ",
        "internet", "utility", "spectrum", "t mobile",
    ],
    "Entertainment": ["netflix", "hulu", "spotify", "disney", "hbo", "movie", "theatre", "theater", "amc ", "steam"],
    "Shopping": ["amazon", "target", "walmart", "best buy", "ebay", "etsy"],
    "Travel": ["airline", "delta ", "united ", "southwest", "airbnb", "hotel", "marriott", "hilton", "expedia"],
    "Automotive": ["autozone", "jiffy lube", "car wash", "oil change", "mechanic", "dmv"],
    "Health & Wellness": ["pharmacy", "cvs", "walgreens", "gym", "fitness", "clinic", "dental", "doctor"],
    "Money out": ["venmo", "zelle", "paypal transfer", "atm withdrawal", "cash app"],
    "Fees & Adjustments": ["fee", "interest charge", "adjustment", "late payment"],
}


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_override(description: str, category: str, path: Path = OVERRIDES_PATH) -> None:
    overrides = load_overrides(path)
    overrides[description] = category
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(overrides, f, indent=2, sort_keys=True)


def _match_keyword_rules(description: str, keyword_rules: dict[str, list[str]]) -> str | None:
    lowered = description.lower()
    for category, keywords in keyword_rules.items():
        for keyword in keywords:
            if re.search(re.escape(keyword.lower()), lowered):
                return category
    return None


def categorize_transactions(
    df: pd.DataFrame,
    overrides: dict[str, str] | None = None,
    keyword_rules: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Return a copy of ``df`` with a normalized ``Category`` column."""
    overrides = overrides if overrides is not None else load_overrides()
    keyword_rules = keyword_rules if keyword_rules is not None else DEFAULT_KEYWORD_RULES
    original_category = df["Category"] if "Category" in df.columns else None

    df = df.copy()

    def resolve(description: str, idx: int) -> str:
        if description in overrides:
            return overrides[description]
        keyword_match = _match_keyword_rules(description, keyword_rules)
        if keyword_match:
            return keyword_match
        if original_category is not None and pd.notna(original_category.iloc[idx]):
            return str(original_category.iloc[idx])
        return "Uncategorized"

    df["Category"] = [resolve(desc, idx) for idx, desc in enumerate(df["Description"])]
    return df


def uncategorized_merchants(df: pd.DataFrame) -> list[str]:
    return sorted(df.loc[df["Category"] == "Uncategorized", "Description"].unique().tolist())
