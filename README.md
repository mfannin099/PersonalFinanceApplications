# PersonalFinanceApplications

Applications to assist with personal finances (Credit Card spending, Stock
selection, budgeting, etc). One shared `uv` project/venv at this root; each
folder is its own standalone Streamlit app with its own entry point.

## Setup

```bash
uv sync
```

## Apps

- **Credit Card** (`CreditCard/app.py`) — upload a credit card statement CSV,
  get automatic merchant categorization (editable/persisted), Venmo-split
  accounting, spend breakdowns, and money-saving flags (recurring charges,
  frequent small purchases, large one-offs, new merchants).
  ```bash
  uv run streamlit run CreditCard/app.py
  ```
- **Stocks** (`Stocks/app.py`) — placeholder, not yet built.
- **Budget** (`Budget/app.py`) — placeholder, not yet built.

## Tests

```bash
uv run pytest
```
