# PersonalFinanceApplications

Personal finance tools. One `uv` project/venv at this root covers all
sub-apps, but each folder is an independent Streamlit app with its own
`app.py` entry point — there is no shared nav shell by design (one app per
folder, run separately).

## Structure

- `CreditCard/` — credit card statement dashboard (built out).
  - `app.py` — entry point (`uv run streamlit run CreditCard/app.py`); sets
    its own `st.set_page_config` and imports `categorize`/`cleaning` as local
    modules (not `CreditCard.categorize`) since Streamlit puts the script's
    own directory on `sys.path`, not the project root.
  - `cleaning.py` — `clean_statement(df)`: validates required columns, keeps
    only `Sale` rows when a `Type` column exists, parses dates, takes
    `abs(Amount)`, strips non-letter characters from `Description` so store
    numbers/codes collapse into one merchant.
  - `categorize.py` — rule-based categorization. Priority order: saved
    per-merchant override → keyword match (`DEFAULT_KEYWORD_RULES`) → the
    statement's own `Category` column if present → `"Uncategorized"`.
    Overrides persist to `CreditCard/data/merchant_overrides.json`
    (gitignored — it's personal data, and not committed to keep the repo
    sharable).
    The UI itself: upload, review/fix uncategorized merchants, Venmo-split
    accounting, spend charts, and the recurring / frequent-small /
    large-one-off / new-merchant "money saving" flags that feed the
    recommendations section.
- `Stocks/app.py`, `Budget/app.py` — placeholder apps, not yet implemented.
- `tests/` — pytest for `CreditCard/cleaning.py` and `CreditCard/categorize.py`
  (imported there via the `CreditCard.` package path, since pytest runs from
  the project root — only each app's own `app.py` uses local imports).

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
- `CreditCard/data/` holds only the user's local override file; it is not
  committed.
