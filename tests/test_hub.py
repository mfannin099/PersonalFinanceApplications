"""Tests for the Finance Hub's pure logic (signals and sample data)."""

import pandas as pd

from CreditCard.categorize import categorize_transactions
from CreditCard.cleaning import clean_statement
from FinanceHub.signals import FLAGS, sample_statement, savings_signals


def _categorized(end="2026-06-30"):
    return categorize_transactions(clean_statement(sample_statement(end=end)), overrides={})


def test_sample_statement_is_deterministic_and_cleans_like_a_real_export():
    a, b = sample_statement(end="2026-06-30"), sample_statement(end="2026-06-30")
    pd.testing.assert_frame_equal(a, b)
    cleaned = clean_statement(a)
    assert "Payment Thank You" not in set(cleaned["Description"])  # Payment rows dropped
    assert (cleaned["Amount"] > 0).all() and cleaned["Transaction Date"].notna().all()


def test_signals_flag_expected_merchants():
    out = savings_signals(_categorized(), months_seen=3, min_count=5, max_avg=25, z_threshold=3, new_days=15)
    assert set(FLAGS) <= set(out["flagged"].columns)
    assert "Netflix" in set(out["recurring"]["Description"])  # billed every month
    assert "Starbucks" in set(out["small"]["Description"])  # many small purchases
    cands = out["candidates"]
    assert (cands["Transactions"] >= 1).all()
    assert cands["Total Amount"].is_monotonic_decreasing


def test_candidates_require_two_signals():
    out = savings_signals(_categorized())
    flagged = out["flagged"]
    assert set(out["candidates"]["Description"]) == set(flagged.loc[flagged["cut_score"] >= 2, "Description"])


def test_signals_on_empty_frame_returns_empty_results():
    out = savings_signals(_categorized().iloc[0:0])
    assert out["candidates"].empty and out["recurring"].empty
