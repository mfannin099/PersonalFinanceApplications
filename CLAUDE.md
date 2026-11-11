# PersonalFinanceApplications

Personal finance tools. One `uv` project/venv at this root covers everything.

**`FinanceHub/` is the final, recommended application** — a single Shiny for
Python app (Home / Spending / Budget / Stocks) and the one to use as the
personal finance hub. It was chosen over Streamlit and Dash because Shiny's
reactive model suits the expensive, button-gated work (Yahoo fetches, XGBoost)
and its modules let the three apps share one shell and one uploaded
statement. `CreditCard/` (Streamlit), `Budget/` (Dash) and `Stocks/` (Shiny)
remain as standalone apps with their own `app.py`, but they are now **legacy
entry points**: their pure logic modules (`analytics`, `categorize`,
`cleaning`, `budgeting`, `export`, `statement`, `forecast`, ...) are reused by
the hub, so keep them, and put new features in the hub rather than extending
the standalone UIs.

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
      budget with no translation layer), plus four categories that never
      appear on a card statement: `Rent/Mortgage`, `Savings`, `Investing`,
      `Debt Payments` (`Investing` shares the 50/30/20 savings bucket, weight
      10; under Pay Yourself First it's weighted in the remainder, not the
      off-the-top savings). Exceptions to "verbatim": `"Money out"` and
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
      precision budgeting model. Each split rounds to cents and hands any
      leftover cent to its heaviest-weighted category, so every preset sums
      to income exactly (no stray "-$0.01 unallocated"). `Zero-Based` differs from `50/30/20` by
      skipping the Needs/Wants/Savings tiering and weighting every category
      against every other directly — `Savings` competes on the same
      footing as spending categories instead of a guaranteed 20% floor.
    - `budget_vs_actual` — budgeted vs. actual $ per category, with
      variance/%-used/status, for driving the progress bars. The four
      non-card categories never appear on a card statement, so their actual
      comes only from the optional user-entered `manual_actuals` argument
      ({category: $}, 0 if missing). In `Budget/app.py` these are four `$`
      inputs under "Manually tracked spending" at the top of the Budget vs.
      Actual card (session-only, like everything else); the rows show a
      "Manually entered" note and have no drill-down accordion item.
      Transactions categorized `"Money out"` or
      `"Fees & Adjustments"` are excluded entirely — not shown as $0, just
      not counted anywhere in the Budget app.
    - `budget_summary` — total income/budgeted/unallocated, for the
      Allocations card's summary row.
    - `filter_to_recent_window` — a budget is a monthly plan, so an
      uploaded statement is trimmed to its most recent `RECENT_WINDOW_DAYS`
      (30) before categorization/budget-vs-actual, instead of comparing
      against its full history. Anchored to the statement's own latest
      `Transaction Date`, not wall-clock time, same reasoning as the "new
      merchant" window in `CreditCard/app.py`. Applied once, right after
      `clean_statement`, so both `cleaned-transactions-store` and
      `transactions-store` only ever hold the filtered window.
  - `statement.py` — pure `read_statement_csv(bytes)` used by the upload
    callback (unit-tested in `tests/test_statement.py`): rejects files over
    `MAX_UPLOAD_BYTES` (5 MB, also set as `dcc.Upload(max_size=...)`),
    decodes UTF-8 with or without a BOM and falls back to cp1252/latin-1,
    and strips whitespace from header names — so real-world card exports
    don't fail `clean_statement`'s required-column check. Lives in
    `Budget/`, not `CreditCard/cleaning.py`, to leave the CreditCard app
    untouched.
  - Input validation in `app.py`: number inputs intentionally have no HTML
    `min`/`max` (Dash reports an out-of-range value as `None`, hiding it from
    validation). Instead negative income, tax/savings rates outside 0-100,
    and negative manual actuals are flagged `invalid` with a `FormFeedback`
    message (negative manual actuals count as $0). Allocation cells reject
    non-numeric input (`on_change` validate/reject), and negative amounts
    are highlighted red with a warning listing them. Generate Suggested
    Budget asks for confirmation in a modal when step 2 already has
    non-zero amounts, since it overwrites them.
  - `export.py` — pure, Dash-free `budget_to_csv`/`budget_to_pdf` (the PDF
    uses `reportlab`; unit-tested in `tests/test_export.py`). Wired to the
    CSV/PDF buttons in the Allocations card header via `dcc.Download`. Export
    is the plan (Category/Budgeted) plus Actual/Variance/Status when a
    statement is uploaded or any manual actual was entered.
    There is no "Load budget CSV" yet, so a saved file is for reference, not
    restore — session-only state is otherwise unchanged.
  - `assets/style.css` — Dash auto-serves `Budget/assets/`; holds the app's
    custom styling (Inter font, gradient header band, card/progress/dropzone/
    status-chip classes). `app.py` uses these classes (`app-header`,
    `app-shell`, `step-pill`, `cat-row`, `dropzone`, ...) plus a shared
    `TABLE_STYLE` for DataTables. Cards are numbered steps 1-4 (Income &
    Method -> Allocations -> Upload -> Budget vs. Actual). The Allocations
    card has a Take-home income/Budgeted/Unallocated summary + meter and a
    status chip (inside the card, not a top-of-page KPI row — the old
    header "Unallocated" badge was removed as a duplicate of it);
    unallocated within $1 counts as balanced (green) to absorb preset
    rounding. The Budget vs. Actual card likewise opens with its own
    Spent/Budgeted/Remaining summary + over-budget count, hides categories
    with $0 budgeted and $0 spent, and sorts the progress rows and chart
    largest-first in the same order (the chart uses an explicit
    `categoryarray`). With nothing budgeted or spent it shows an
    `empty_state` instead. A footer states that state is session-only.
  - Reuses the shared `CreditCard/data/merchant_overrides.json` for
    merchant categorization — that's categorization data, not budget data.
    The budget itself (income, chosen preset, allocations) is **session
    -only, not persisted** (see Notes below).
- `Stocks/` — "Stock Studio", a **Shiny for Python** app (replaced an
  earlier Streamlit "coming soon" placeholder). Multi-ticker (up to 5)
  price analysis from Yahoo Finance via `yfinance`.
  - `studio.py` — the whole UI + server as a Shiny module (`stocks_ui`/
    `stocks_server`), imported package-qualified (`Stocks.forecast`) and shared
    by `app.py` and `FinanceHub`. `app.py` is a thin standalone shell
    (`uv run shiny run Stocks/app.py`; puts the project root on `sys.path`).
    The rest of this section describes the module's behaviour. Charts are Plotly via `shinywidgets` (`render_widget`).
    Prices are fetched only on "Run analysis" (`@reactive.event(input.run,
    ignore_none=False)`, so it also runs once on load); MA type/windows and
    forecast horizon recompute live on the loaded data. Overview tab: growth
    of $1,000 chart + fundamentals table. Forecast tab: per-ticker value
    boxes, price + short/long MA chart with crossover markers and the trend
    forecast/band, and a recent-crossovers table. The sidebar settings use
    plain-language labels (Fast/Slow average, Project ahead) with a hint
    under each, 20/50 · 50/200 · Reset buttons, a warning when the loaded
    history is too short for the slow average (the forecast needs ~2x the
    slow window), and a collapsible "How the forecast works" card. Financials
    tab: annual revenue / net income / FCF bar chart for a chosen ticker, then
    grouped comparison tables (Income statement, Growth, Profitability,
    Valuation, Financial health, Cash flow, Shareholder return, Analyst view)
    with rule-of-thumb green/red cues. Styling in
    `Stocks/style.css` mirrors Budget Studio's look.
  - `forecast.py` — pure: `moving_average` (SMA/EMA), `crossovers`
    (Bullish = short MA crosses above long), and `ma_trend_forecast`: daily
    growth = slope of a line fit to log(long MA) over its last `window`
    points, projected from the **last close** (not the lagging MA); an 80%
    band from 60-day daily log-return volatility widening with sqrt(time).
    Future dates are business days (holidays not excluded). A simple,
    explainable trend projection, deliberately not a predictive model.
  - `financials.py` — pure: `annual_table(income, balance, cashflow)` builds a
    per-fiscal-year table from yfinance's statements (margins, growth, FCF
    conversion, interest coverage, debt/equity, ROE/ROA/ROIC; zero
    denominators give NaN, not inf), and `investor_metrics(infos, annuals)`
    returns grouped `(label, [(text, cue)])` rows per ticker, preferring
    Yahoo's trailing `info` figure and falling back to the latest fiscal year.
    Also holds the reading guide shown on the Financials tab: `GLOSSARY`
    (metric -> what it is / how to read it; a test keeps it in sync with the
    metric rows in `SECTIONS`), `SECTION_INTRO`, `READING_ORDER` and
    `GUIDE_CAVEATS`. Cue thresholds quoted in the glossary must match the
    rules in `SECTIONS`.
    Self-contained (own formatters) so it imports the same way locally and in
    tests. The health cues are rule-of-thumb thresholds, not advice.
  - `ml_forecast.py` — pure: a small XGBoost model (button-triggered "ML
    Forecast" tab) predicting the **forward return** over the horizon, not the
    price (trees can't extrapolate price levels). Time-based features only
    (lagged returns, momentum, volatility, distance from a fast/slow moving
    average, day of week/month). The moving-average features are customizable
    (SMA or EMA, fast/slow windows, default SMA 20/50; `clean_ma`/`DEFAULT_MA`,
    feature names `dist_ma_fast`/`dist_ma_slow`). `FEATURE_INFO` explains every
    feature in plain language and feeds the "What each feature means" card
    (with an Importance column after training) and the importance chart labels. Fixed shallow params (`PARAMS`, depth 2) — deliberately no
    tuning. Validated by expanding-window walk-forward with a `horizon`-day
    purge gap and scored vs. naive baselines (skill, hit rate vs. always-up,
    correlation); the 80% band is the empirical 10-90% quantile of OOS errors.
    The ML tab loads its **own** price history (`ML_HISTORY` select, default
    5y) on each Train click instead of reusing the sidebar lookback — the
    default 1y lookback is too short to train/validate, so every setting
    change used to hit the same "usable days" error. Results carry a
    settings signature; changing any input afterward shows a "results are out
    of date, click Train" banner (training only runs on the button).
    Tunable from a collapsible "Model settings" panel: feature on/off by group,
    seven hyper-parameters (clamped to `PARAM_BOUNDS`), and an optional
    **nested** auto-tune over a tiny grid (`TUNE_GRID`: depth 1-3 x learning
    rate 0.02/0.05) — candidates are scored by inner walk-forward using only
    each fold's training window, so tuning can't leak into the scorecard. The
    scorecard also shows in-sample vs. out-of-sample error to expose overfit.
    On real stocks it usually shows no skill — that's the honest result and
    the UI says so. Needs `xgboost` + `scikit-learn`; on macOS also
    `brew install libomp` (XGBoost's OpenMP runtime).
  - `snapshot.py` — pure: `price_snapshot(closes)` (last price, day change,
    period return, low/high and range position per ticker) and
    `sparkline_points` (SVG polyline points), feeding the Overview's
    quick-glance ticker cards.
  - UI polish (see `style.css`'s "Polish pass"): tabs have ids
    (`main_tabs`: overview/forecast/ml/financials) so the sidebar only shows
    forecast settings on the tabs that use them; KPI tiles are custom
    `.metric-card` divs (bslib's value_box stacked the icon above the text in
    narrow cards); card headers stack title over subtitle; plotly toolbars
    show on hover; comparison tables have sticky headers/first column; the
    Financials guide is a collapsed `<details>`. `style.css` is inlined at
    app start, so restart the server to see CSS edits.
  - `fundamentals.py` — pure: `fundamentals_table(infos)` formats Yahoo's
    `info` dict into a metric x ticker table, `—` for missing fields.
    Note yfinance 1.x reports `dividendYield` already as a percent (0.33 =
    0.33%) but `profitMargins` as a fraction.
  - `market_data.py` — `parse_tickers`/`rebase` (to `START_VALUE` = $1,000) are pure;
    `fetch_closes`/`fetch_info`/`fetch_statements` hit Yahoo and are `lru_cache`d keyed by
    today's date. Unknown tickers come back from Yahoo as all-NaN columns,
    not errors, so `fetch_closes` drops and reports them.
- `FinanceHub/` — the blended app: Spending + Budget + Stocks in one **Shiny
  for Python** app (`uv run shiny run FinanceHub/app.py`). `page_navbar` with
  Home / Spending / Budget / Stocks; each section is a Shiny module
  (`@module.ui`/`@module.server`). `app.py` puts the project root on
  `sys.path` and imports everything package-qualified.
  - `shared.py` — `StatementState`: one uploaded statement (`cleaned`) and a
    `categorized()` calc shared by Spending and Budget; saving a category
    override calls `bump()` so both recompute. Also KPI/card/figure helpers.
  - `home.py` — upload (via `Budget.statement.read_statement_csv` +
    `clean_statement`), a synthetic "Try sample data" button, and the
    category review (one "Save" button; only changed merchants are written to
    the shared `merchant_overrides.json`).
  - `spending.py` — the CreditCard dashboard ported to Plotly: filters,
    Venmo splits (select rows in a data grid, then mark as split — replaces
    Streamlit's editable table), the overview/weekday/over-time/category-over
    -time charts, and Money Saving signals. Filters fall back to "everything"
    until the (possibly collapsed) sidebar renders them.
  - `signals.py` — pure `savings_signals` (the four signals + recommendations)
    and `sample_statement`; tested in `tests/test_hub.py`.
  - `budget.py` — the Budget Studio planner ported: income/Gross-Net, preset
    suggestions into per-category numeric inputs (replace-confirm modal),
    manual actuals, progress rows + chart on the last 30 days, CSV/PDF export.
    Reuses `Budget.budgeting`/`Budget.export` unchanged. Session-only.
  - Stocks is mounted from `Stocks/studio.py` (see below).
- `tests/` — pytest for `CreditCard/cleaning.py`, `CreditCard/categorize.py`,
  `CreditCard/analytics.py`, `Budget/budgeting.py`, `Budget/export.py`,
  `Budget/statement.py`, `FinanceHub/signals.py` (`tests/test_hub.py`), and `Stocks/`'s pure modules (`tests/test_stocks.py`,
  synthetic data only — never the network) (imported there via
  the `CreditCard.`/`Budget.` package paths, since pytest runs from the
  project root — only each Streamlit app's own `app.py` uses local imports;
  `Budget/app.py` is package-qualified even outside tests, see above).

This replaces the earlier one-off prototype at
`ad_hoc/finances_streamlit_tracker` — that version is left in place as
historical reference but is no longer maintained.

## Running

```bash
uv run shiny run FinanceHub/app.py   # the main app: Finance Hub (Shiny)
# Legacy standalone apps:
uv run streamlit run CreditCard/app.py
uv run python -m Budget.app   # Dash, not Streamlit — see Structure above
uv run shiny run Stocks/app.py   # Shiny for Python
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
- The "Income Type" (Gross/Net) toggle is functional: selecting Gross reveals
  an "Estimated Tax Rate (%)" input (default 22) and a Net Monthly Income
  readout, and `budgeting.net_income(income, income_type, tax_rate_pct)`
  takes that flat effective rate off. Everything downstream (suggested
  allocations, the Allocations summary, exports) is built on the net
  amount; there is no real withholding/bracket calculation. CSV/PDF exports
  list Gross income, tax rate and Net income when Gross was selected.
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
- `Budget/app.py`'s visual design was deliberately reworked by request,
  informed by a look at YNAB/Monarch Money/Copilot Money's UI/UX: titled
  "Budget Studio" (`app.title`, not just the folder name "Budget"), themed
  with `dbc.themes.ZEPHYR` (+ `dbc.icons.BOOTSTRAP` for icon glyphs) instead
  of the bare `dbc.themes.BOOTSTRAP` default, every section header uses a
  Bootstrap Icon (`icon()` helper — `html.I(className="bi bi-...")`)
  instead of emoji, and each card uses `dbc.CardHeader` + `dbc.CardBody`
  rather than a plain `<h5>` inside the body. A top-of-page KPI stat-tile
  row (Monarch/Copilot's "big numbers up top" pattern) was tried and then
  explicitly removed by request — don't re-add it without asking.
