import csv
import io

from Budget.budgeting import budget_vs_actual
from Budget.export import budget_to_csv, budget_to_pdf

import pandas as pd


def _result():
    allocations = pd.DataFrame({"Category": ["Groceries", "Gas"], "Budgeted Amount": [400.0, 150.0]})
    transactions = pd.DataFrame({"Category": ["Groceries"], "Amount": [450.0]})
    return budget_vs_actual(allocations, transactions)


def test_csv_plan_only_has_budget_columns_and_header_block():
    rows = list(csv.reader(io.StringIO(budget_to_csv(5000, "50/30/20", _result(), has_actuals=False))))
    assert rows[0] == ["Monthly Income", "5000.00"]
    assert rows[1] == ["Budgeting Method", "50/30/20"]
    assert rows[3] == ["Unallocated", "4450.00"]
    assert rows[5] == ["Category", "Budgeted Amount"]


def test_csv_with_actuals_includes_variance_and_status():
    rows = list(csv.reader(io.StringIO(budget_to_csv(5000, "50/30/20", _result(), has_actuals=True))))
    header = rows[5]
    assert header == ["Category", "Budgeted Amount", "Actual Amount", "Variance", "Status"]
    groceries = next(r for r in rows[6:] if r[0] == "Groceries")
    assert groceries[1:] == ["400.00", "450.00", "-50.00", "Over"]


def test_pdf_is_valid_pdf_bytes():
    for has_actuals in (False, True):
        assert budget_to_pdf(5000, "Zero-Based", _result(), has_actuals).startswith(b"%PDF")


def test_csv_gross_income_adds_tax_rows():
    rows = list(csv.reader(io.StringIO(budget_to_csv(4000, "50/30/20", _result(), False, 5000, 20))))
    assert rows[0] == ["Gross Monthly Income", "5000.00"]
    assert rows[1] == ["Estimated Tax Rate (%)", "20"]
    assert rows[2] == ["Net Monthly Income", "4000.00"]
    assert rows[3] == ["Budgeting Method", "50/30/20"]


def test_pdf_gross_income_is_valid_pdf():
    assert budget_to_pdf(4000, "50/30/20", _result(), True, 5000, 20).startswith(b"%PDF")
