# PersonalFinanceApplications

Personal finance tools. One `uv` project/venv at this root covers all
sub-apps, but each folder is an independent app (Streamlit or Dash) with its
own `app.py` entry point — there is no shared nav shell by design (one app
per folder, run separately).

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
- `Budget/` — budgeting dashboard (built out), the one **Dash** app in the
  repo (not Streamlit).
  - `app.py` — entry point, run via `uv run python -m Budget.app` (module
    execution, not `python Budget/app.py`) because plain script execution
    puts `Budget/` itself on `sys.path[0]` instead of the project root,
    which would break its package-qualified imports. Unlike the Streamlit
    apps, `app.py` imports `CreditCard.cleaning`/`categorize`/`analytics`
    and `Budget.budgeting` fully package-qualified throughout — Dash has no
    Streamlit-style sys.path trick, so there's no local-import variant.
  - `budgeting.py` — pure, Dash-free budgeting logic (unit-tested in
    `tests/test_budgeting.py`), parallel to `CreditCard/analytics.py`:
    - `BUDGET_CATEGORIES` — `CreditCard.categorize.CATEGORIES` reused
      verbatim (so a categorized card transaction maps into the budget with
      no translation layer) plus five categories that never appear on a
      card statement: `Rent/Mortgage`, `Insurance`, `Savings`,
      `Debt Payments`, `Childcare`.
    - `suggested_allocations` — starting-point $ allocations for one of four
      preset methods (`50/30/20`, `Zero-Based`, `Envelope`,
      `Pay Yourself First`); these seed the editable allocations table, they
      aren't a final answer. `Envelope` currently shares `50/30/20`'s
      formula — the methods differ in UI framing (hard-cap styling), not
      allocation math. Within any bucket (or across everything, for
      Zero-Based), the split uses `CATEGORY_WEIGHTS` — not an even split —
      so e.g. `Rent/Mortgage` is suggested a realistic multiple of `Fees &
      Adjustments` rather than an identical share; the weights are rough
      typical-household proportions, not a precision budgeting model.
    - `budget_vs_actual` — budgeted vs. actual $ per category, with
      variance/%-used/status, for driving the progress bars. The five
      non-card categories always get `Actual Amount == 0` since they never
      appear on a card statement.
    - `budget_summary` — total income/budgeted/unallocated, for the
      "Unallocated" badge.
    - `filter_to_recent_window` — a budget is a monthly plan, so an
      uploaded statement is trimmed to its most recent `RECENT_WINDOW_DAYS`
      (30) before categorization/budget-vs-actual, instead of comparing
      against its full history. Anchored to the statement's own latest
      `Transaction Date`, not wall-clock time, same reasoning as the "new
      merchant" window in `CreditCard/app.py`. Applied once, right after
      `clean_statement`, so both `cleaned-transactions-store` and
      `transactions-store` only ever hold the filtered window.
  - Reuses the shared `CreditCard/data/merchant_overrides.json` for
    merchant categorization — that's categorization data, not budget data.
    The budget itself (income, chosen preset, allocations) is **session
    -only, not persisted** (see Notes below).
- `Stocks/app.py` — placeholder app, not yet implemented.
- `tests/` — pytest for `CreditCard/cleaning.py`, `CreditCard/categorize.py`,
  `CreditCard/analytics.py`, and `Budget/budgeting.py` (imported there via
  the `CreditCard.`/`Budget.` package paths, since pytest runs from the
  project root — only each Streamlit app's own `app.py` uses local imports;
  `Budget/app.py` is package-qualified even outside tests, see above).

This replaces the earlier one-off prototype at
`ad_hoc/finances_streamlit_tracker` — that version is left in place as
historical reference but is no longer maintained.

## Running

```bash
uv run streamlit run CreditCard/app.py
uv run python -m Budget.app   # Dash, not Streamlit — see Structure above
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
- `Budget/` has no persistence, by deliberate choice mirroring `CreditCard`'s
  "no database" philosophy: income, chosen preset, and allocations are
  session-only and reset on every run. It never writes to a `Budget/data/`
  folder (none exists). Only `CreditCard/data/merchant_overrides.json` is
  read/written by `Budget/app.py`, and that's shared, pre-existing
  categorization state, not budget state.
