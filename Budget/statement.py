"""Turning an uploaded statement file into a DataFrame — pure, Dash-free.

Card exports aren't uniform: some start with a UTF-8 byte-order mark, some
are Windows-1252 encoded, and some pad header names with spaces. Any of
those would otherwise surface as a confusing "missing required columns"
error from ``clean_statement``, so this normalizes them first.
"""

import io

import pandas as pd

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB — years of card history is well under 1 MB

# ``utf-8-sig`` also handles plain UTF-8 (the BOM is optional); ``cp1252`` is
# what Excel on Windows writes. ``latin-1`` decodes any byte sequence, so it
# is the last resort that always succeeds.
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def decode_statement(raw: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("unreachable: latin-1 decodes any bytes")


def read_statement_csv(raw: bytes) -> pd.DataFrame:
    """Parse statement CSV bytes, with header names stripped of surrounding whitespace.

    Raises ``ValueError`` if the file is too large, empty, or not parseable as CSV.
    """
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        df = pd.read_csv(io.StringIO(decode_statement(raw)))
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError("File isn't a readable CSV.") from exc
    df.columns = [str(c).strip() for c in df.columns]
    return df
