# PersonalFinanceApplications

Applications to assist with personal finances (Credit Card spending, Stock
selection, budgeting, etc). One shared `uv` project/venv at this root; each
folder is its own standalone Streamlit app with its own entry point.

## Setup

```bash
uv sync
```

## Apps

- **Credit Card** (`CreditCard/app.py`) — upload a credit card statement CSV
  and get:
  - Automatic merchant categorization, editable and persisted per-merchant.
  - Venmo-split accounting (mark transactions as split/reimbursed).
  - Sidebar filters: merchant, category, date range, amount range.
  - Spending Overview: top-N category/merchant/frequency charts (adjustable
    N) and a Share of Wallet donut (% of spend by category).
  - Spending by Day of Week: a combo chart toggling between Total/Average/
    Median $ (bars) with average transaction count overlaid (line), plus
    overall median daily spend / average transactions-per-day stats — all
    computed across every calendar day in the statement, including days
    with no purchases.
  - Spending Over Time: an aggregate line chart (Daily/Weekly/Monthly),
    a drilldown to see exactly what was purchased in a selected period, and
    a per-category breakout chart with its own category picker.
  - Money Saving Opportunities: recurring charges, frequent small purchases,
    large one-off purchases (judged against each category's own typical
    spend), and newly-seen merchants — rolled into a Recommendations list.

  ```bash
  uv run streamlit run CreditCard/app.py
  ```
- **Stocks** (`Stocks/app.py`) — placeholder, not yet built.
- **Budget** (`Budget/app.py`) — placeholder, not yet built.

## Tests

```bash
uv run pytest
```
