# PersonalFinanceApplications

Applications to assist with personal finances (Credit Card spending, Stock
selection, budgeting, etc). One `uv` project/venv at this root, with a
multi-page Streamlit app wiring each sub-app together.

## Setup

```bash
uv sync
uv run streamlit run Home.py
```

## Apps

- **Credit Card** (`CreditCard/`) — upload a credit card statement CSV, get
  automatic merchant categorization (editable/persisted), Venmo-split
  accounting, spend breakdowns, and money-saving flags (recurring charges,
  frequent small purchases, large one-offs, new merchants).
- **Stocks** (`Stocks/`) — placeholder, not yet built.
- **Budget** (`Budget/`) — placeholder, not yet built.

## Tests

```bash
uv run pytest
```
