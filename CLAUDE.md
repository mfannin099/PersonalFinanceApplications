# PersonalFinanceApplications

Multi-page Streamlit app for personal finance tools. One `uv` project/venv at
this root covers all sub-apps.

## Structure

- `Home.py` — entry point; wires up `st.navigation` across the sub-apps.
- `CreditCard/` — credit card statement dashboard (built out).
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
  - `page.py` — the Streamlit UI: upload, review/fix uncategorized
    merchants, Venmo-split accounting, spend charts, and the recurring /
    frequent-small / large-one-off / new-merchant "money saving" flags that
    feed the recommendations section.
- `Stocks/`, `Budget/` — placeholder pages, not yet implemented.
- `tests/` — pytest for `cleaning.py` and `categorize.py`.

This replaces the earlier one-off prototype at
`ad_hoc/finances_streamlit_tracker` — that version is left in place as
historical reference but is no longer maintained.

## Running

```bash
uv run streamlit run Home.py
uv run pytest
```

## Notes / known decisions

- Categorization intentionally has no database — it's CSV-upload-per-session
  like the original prototype. The improvement over the prototype is the
  categorization *engine* (keyword rules + persisted per-merchant overrides),
  not data persistence across statements.
- `CreditCard/data/` holds only the user's local override file; it is not
  committed.
