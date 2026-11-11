# PersonalFinanceApplications

Personal finance tools in one `uv` project. **The app to use is the Finance
Hub** — a single Shiny for Python app that brings spending analysis,
budgeting and stock research together. The three original apps still run on
their own, but the hub supersedes them.

## Setup

```bash
uv sync
```

On macOS, the Stocks ML forecast also needs XGBoost's OpenMP runtime:
`brew install libomp`.

## Finance Hub (recommended)

```bash
uv run shiny run FinanceHub/app.py
```

- **Home** — upload a credit card CSV once (or try the synthetic sample data),
  check merchant categories.
  Category fixes are remembered per merchant and apply everywhere.
- **Spending** — filters, Venmo splits, spend by category/merchant/weekday/time,
  share of wallet, needs vs. wants, and money-saving opportunities
  (recurring, frequent small, large one-off and new merchants).
- **Budget** — income (net or gross), 50/30/20, Zero-Based or Pay Yourself
  First suggestions, editable allocations, and budget vs. actual on the last
  30 days of your statement, with CSV/PDF export. Session-only; nothing is
  saved between runs.
- **Stocks** — compare up to 5 tickers (growth of $1,000, fundamentals),
  moving-average forecast, an XGBoost ML forecast with an honest scorecard
  (customizable SMA/EMA trend features, plus a plain-language guide to every
  feature), and annual financials with a reading guide.

Statement data stays in the session; stock data comes from Yahoo Finance.
Educational use only, not financial or investment advice.

## Standalone apps (legacy)

Each folder is a self-contained app; the hub reuses their logic.

```bash
uv run streamlit run CreditCard/app.py   # Streamlit: statement dashboard
uv run python -m Budget.app              # Dash: Budget Studio
uv run shiny run Stocks/app.py           # Shiny: Stock Studio
```

## Tests

```bash
uv run pytest
```
