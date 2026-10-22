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
    - `BUDGET_CATEGORIES` — most of `CreditCard.categorize.CATEGORIES`
      reused verbatim (so a categorized card transaction maps into the
      budget with no translation layer), plus three categories that never
      appear on a card statement: `Rent/Mortgage`, `Savings`,
      `Debt Payments`. Exceptions to "verbatim": `"Money out"` and
      `"Fees & Adjustments"` are excluded entirely (not budget line items —
      see Notes below); `"Shopping"` is relabeled `"Misc Shopping"`; and
      `"Bills & Utilities"` is relabeled `"Bills & Utilities (+ Insurance)"`
      — `Insurance` was a separate non-card category but is now folded into
      this one line instead (its own `CATEGORY_WEIGHTS` entry was added
      into `Bills & Utilities`'s). Both relabels go through
      `relabel_for_budget`/`_CARD_CATEGORY_RELABELS`.
    - `relabel_for_budget` — renames a categorized statement's `Category`
      values to match Budget's labels (`"Shopping"` -> `"Misc Shopping"`,
      `"Bills & Utilities"` -> `"Bills & Utilities (+ Insurance)"`). Used
      wherever a transaction's raw CreditCard category needs to line up
      with `BUDGET_CATEGORIES` — inside `budget_vs_actual` itself, and in
      `Budget/app.py`'s drill-down accordion (which classifies with the raw
      category first via `with_classification`, *then* relabels, so the
      Essential/Discretionary mapping isn't broken by the rename).
    - `suggested_allocations` — starting-point $ allocations for one of
      three preset methods (`50/30/20`, `Zero-Based`, `Pay Yourself First`;
      `Envelope` was considered but removed — see Notes below). These seed
      the editable allocations table, they aren't a final answer. Within
      any bucket (or across everything, for `Zero-Based`), the split uses
      `CATEGORY_WEIGHTS` — not an even split — so e.g. `Rent/Mortgage` is
      suggested a realistic multiple of `Gas` rather than an identical
      share; the weights are rough typical-household proportions, not a
      precision budgeting model. `Zero-Based` differs from `50/30/20` by
      skipping the Needs/Wants/Savings tiering and weighting every category
      against every other directly — `Savings` competes on the same
      footing as spending categories instead of a guaranteed 20% floor.
    - `budget_vs_actual` — budgeted vs. actual $ per category, with
      variance/%-used/status, for driving the progress bars. The three
      non-card categories always get `Actual Amount == 0` since they never
      appear on a card statement. Transactions categorized `"Money out"` or
      `"Fees & Adjustments"` are excluded entirely — not shown as $0, just
      not counted anywhere in the Budget app.
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
- `Budget/budgeting.py`'s preset list was trimmed from four methods down to
  three: `Envelope` was removed and stays removed — it never had a distinct
  allocation formula (it reused `50/30/20`'s, differing only in intended UI
  framing that was never built). `suggested_allocations("Envelope", …)`
  raises `ValueError` like any other unrecognized method name. `Zero-Based`
  was removed in the same pass, then explicitly re-added by request — it's
  weighted across *all* of `BUDGET_CATEGORIES` at once rather than tiered
  into Needs/Wants/Savings buckets like `50/30/20`, which is a real
  behavioral difference (`Savings` has no guaranteed floor under
  `Zero-Based`).
- The "Income Type" (Gross/Net) toggle in `Budget/app.py` is **not read by
  any callback** — it's a caption-only label. Selecting "Gross" changes
  nothing about `suggested_allocations` or any other calculation; the
  entered dollar amount is used as-is either way. This was a deliberate v1
  scope decision (no automatic tax/withholding conversion), but the toggle
  having zero functional effect is easy to miss since it looks interactive.
- `Budget/budgeting.py`'s category list was trimmed/merged by request:
  `Childcare` (a `NON_CARD_CATEGORIES` entry) was dropped outright;
  `"Money out"` and `"Fees & Adjustments"` (CreditCard categories) are
  excluded from every Budget view — a transaction categorized either way
  by CreditCard simply isn't counted anywhere in the Budget app, it
  doesn't fall back into another bucket; `"Shopping"` reads as
  `"Misc Shopping"` in Budget; and the non-card `Insurance` category was
  folded into `"Bills & Utilities"`, which now reads as
  `"Bills & Utilities (+ Insurance)"` — one budget line covering both, with
  actual spend still coming only from card transactions categorized
  `"Bills & Utilities"` (Insurance itself was never a card category, so it
  never contributed actual $ anyway). CreditCard itself is untouched —
  its own taxonomy, keyword rules, and tests still use `"Shopping"`,
  `"Money out"`, `"Fees & Adjustments"`, and `"Bills & Utilities"`
  unchanged, since that's a separate, already-tested app.
- `build_needs_wants_section` in `Budget/app.py` renders the Needs/Wants/
  Other split as one full-width horizontal **stacked bar** (3 segments,
  categorical palette slots 1/2/3 — blue/orange/aqua) with a centered
  legend row of `$amount (pct%)` chips below it, not a donut. A donut asks
  the reader to compare angles across slices; a single stacked bar reads
  part-to-whole directly through segment width, and direct $/% labels live
  in the legend row rather than inside segments since a small slice (e.g.
  a near-zero "Other") can be too narrow to hold text. Replaces an earlier
  donut chart that was also laid out in a half-width `dbc.Col`, which read
  as off-center; the stacked bar is full-width so centering isn't a
  separate layout concern.
- `build_category_review` in `Budget/app.py` (formerly
  `build_uncategorized_review`) lists **every** merchant in the uploaded
  statement with a dropdown to fix its category, not just the ones flagged
  `"Uncategorized"` — a keyword rule or a stale override can categorize a
  merchant *wrong*, not just leave it unresolved, and the user needs a way
  to correct that too. Each dropdown defaults to the merchant's *current*
  category (never forced to `"Uncategorized"`), and the `recategorize_merchants`
  callback (formerly `resolve_uncategorized`) only calls `save_override` for
  a merchant whose dropdown value differs from a `{merchant: category}`
  baseline snapshot (`category-baseline-store`) taken when the review was
  last rendered — this is what stops every page render from silently
  "locking in" a keyword-rule-derived category as a permanent override the
  user never actually chose. The review list itself is collapsed by default
  unless something is actually `"Uncategorized"` (`is_open=uncategorized_count > 0`),
  behind a toggle button so a long merchant list doesn't dominate the page.
