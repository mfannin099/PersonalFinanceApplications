import pytest

from Budget.statement import MAX_UPLOAD_BYTES, read_statement_csv

CSV = "Transaction Date,Description,Amount\n10/01/2026,CAFÉ ROUGE,-12.50\n"


def test_reads_plain_utf8():
    df = read_statement_csv(CSV.encode("utf-8"))
    assert list(df.columns) == ["Transaction Date", "Description", "Amount"]
    assert df.loc[0, "Description"] == "CAFÉ ROUGE"


def test_strips_utf8_byte_order_mark():
    df = read_statement_csv(CSV.encode("utf-8-sig"))
    assert df.columns[0] == "Transaction Date"


def test_falls_back_to_windows_1252():
    df = read_statement_csv(CSV.encode("cp1252"))
    assert df.loc[0, "Description"] == "CAFÉ ROUGE"


def test_strips_whitespace_around_headers():
    df = read_statement_csv(b" Transaction Date , Description ,Amount \n10/01/2026,X,-1\n")
    assert list(df.columns) == ["Transaction Date", "Description", "Amount"]


def test_rejects_empty_file():
    with pytest.raises(ValueError, match="readable CSV"):
        read_statement_csv(b"")


def test_rejects_oversized_file():
    with pytest.raises(ValueError, match="larger than"):
        read_statement_csv(b"x" * (MAX_UPLOAD_BYTES + 1))
