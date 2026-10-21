# PersonalFinanceApplications

Personal finance tools. One `uv` project/venv at this root covers all
sub-apps, but each folder is an independent Streamlit app with its own
`app.py` entry point — there is no shared nav shell by design (one app per
folder, run separately).

## Structure

- `CreditCard/` — credit card statement dashboard (built out).
  - `app.py` — entry point (`uv run streamlit run CreditCard/app.py`); sets
    its own `st.set_page_config` and imports `analytics`/`categorize`/
    `cleaning` as local modules (not `CreditCard.analytics`) since Streamlit
    puts the script's own directory on `sys.path`, not the project root.
  - `cleaning.py` — `clean_statement(df)`: validates required columns, keeps
    only `Sale` rows when a `Type` column exists, parses dates, takes
    `abs(Amount)`, strips non-letter characters from `Description` so store
    numbers/codes collapse into one merchant, and drops `Source File`/
    `Memo`/`Type` (not useful in the editable table).
  - `categorize.py` — rule-based categorization. Priority order: saved
    per-merchant override → keyword match (`DEFAULT_KEYWORD_RULES`) → the
    statement's own `Category` column if present → `"Uncategorized"`.
    Overrides persist to `CreditCard/data/merchant_overrides.json`
    (gitignored — it's personal data, and not committed to keep the repo
    sharable). The whole `CreditCard/data/` folder is gitignored, since it's
    also where real uploaded statement CSVs land locally (e.g. via the local
    `union_statements.py` helper, also gitignored — never commit it or its
    output).
  - `analytics.py` — pure, Streamlit-free analytics helpers (unit-tested in
    `tests/test_analytics.py`):
    - `spend_by_period_and_category` — category breakout of the time-series
      chart; categories outside the top N by spend bucket into `"Other"`.
    - `category_zscores` — flags "large one-off" purchases relative to each
      transaction's own category mean/std (not the whole statement), so a
      category's own typical spend sets the bar.
    - `spend_by_weekday` — Total/Average/Median $ and average transaction
      count by day of week, built over *every calendar day* in the
      statement's date range (including zero-spend days), not just days
      that had a purchase.
    - `daily_spending_stats` — overall median daily spend and average daily
      transaction count, same "every calendar day" basis.
    - `transactions_for_period` — maps a selected point on the Spending Over
      Time chart (a day/week/month resample-bucket label) back to the
      underlying transactions, for the drilldown table.
    - `share_of_wallet` — category totals as % of total spend (donut chart).
  - `app.py` UI, top to bottom: upload → review/fix uncategorized merchants
    → sidebar filters (merchant, category, date range, amount range) →
    editable transactions table (Venmo-split accounting) → summary metrics
    → Spending Overview (top-N category/merchant/frequency bar charts, each
    with a user-adjustable top-N slider, plus a centered Share of Wallet
    donut) → Spending by Day of Week (one bar+line combo chart: a
    Total/Average/Median $ bar toggle with avg-transaction-count overlaid on
    an independent secondary axis, plus an overall median/average caption)
    → Spending Over Time (aggregate line chart with a Daily/Weekly/Monthly
    toggle, a period-drilldown selectbox + transactions table below it, and
    a per-category breakout chart with its own category multiselect and a
    Stacked Area/Lines toggle) → Money Saving Opportunities (recurring
    merchants, frequent small purchases, per-category large one-offs, new
    merchants) → Recommendations (merchants flagged by ≥2 of those signals).
- `Stocks/app.py`, `Budget/app.py` — placeholder apps, not yet implemented.
- `tests/` — pytest for `CreditCard/cleaning.py`, `CreditCard/categorize.py`,
  and `CreditCard/analytics.py` (imported there via the `CreditCard.`
  package path, since pytest runs from the project root — only each app's
  own `app.py` uses local imports).

This replaces the earlier one-off prototype at
`ad_hoc/finances_streamlit_tracker` — that version is left in place as
historical reference but is no longer maintained.

## Running

```bash
uv run streamlit run CreditCard/app.py
uv run pytest
```

## Notes / known decisions

- Categorization intentionally has no database — it's CSV-upload-per-session
  like the original prototype. The improvement over the prototype is the
  categorization *engine* (keyword rules + persisted per-merchant overrides),
  not data persistence across statements.
- `CreditCard/data/` holds only the user's local override file and any
  locally-uploaded statement CSVs; none of it is committed.
- "Days where spending occurs" stats (median daily spend, average daily
  transaction count, and the per-weekday breakdown) are computed over every
  calendar day in the statement's date range, including days with zero
  purchases — an explicit choice over counting only active days.
- The "new merchant" window anchors to the statement's own latest
  `Transaction Date`, not real wall-clock time — otherwise uploading an old
  statement would misjudge every merchant's age.
- Workflow: features are built on a branch, tested (`uv run pytest` +
  compile check + a headless/live Streamlit smoke test), then merged into
  `main` with `--no-ff` one branch at a time, only once the user confirms.
