"""Budget export helpers — pure (Dash-free), parallel to ``budgeting.py``.

Both exporters take the same inputs: the plan's ``income`` (take-home/net) and ``method``, the
``result`` frame from ``budget_vs_actual`` (one row per budget category), and
``has_actuals`` — whether a statement was uploaded or manual actuals were entered. Without a statement the
export is just the plan (Category/Budgeted); with one it also carries
Actual/Variance/Status.
"""

import csv
import io
from datetime import date

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PLAN_COLUMNS = ["Category", "Budgeted Amount"]
_ACTUAL_COLUMNS = ["Category", "Budgeted Amount", "Actual Amount", "Variance", "Status"]


def _table_frame(result: pd.DataFrame, has_actuals: bool) -> pd.DataFrame:
    return result[_ACTUAL_COLUMNS if has_actuals else _PLAN_COLUMNS]


def _income_rows(income: float, gross_income: float | None, tax_rate_pct: float | None) -> list[list[str]]:
    """Header rows for the income block — gross/tax/net when the user entered gross income."""
    if gross_income is None:
        return [["Monthly Income", f"{income:.2f}"]]
    return [
        ["Gross Monthly Income", f"{gross_income:.2f}"],
        ["Estimated Tax Rate (%)", f"{tax_rate_pct or 0:g}"],
        ["Net Monthly Income", f"{income:.2f}"],
    ]


def budget_to_csv(
    income: float,
    method: str,
    result: pd.DataFrame,
    has_actuals: bool,
    gross_income: float | None = None,
    tax_rate_pct: float | None = None,
) -> str:
    """CSV text: a small ``Field,Value`` header block, a blank row, then the category table.

    Pass ``gross_income``/``tax_rate_pct`` when the budget was built from gross pay;
    ``income`` is always the net amount the allocations are measured against.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    total_budgeted = float(result["Budgeted Amount"].sum())
    writer.writerows(
        [
            *_income_rows(income, gross_income, tax_rate_pct),
            ["Budgeting Method", method],
            ["Total Budgeted", f"{total_budgeted:.2f}"],
            ["Unallocated", f"{income - total_budgeted:.2f}"],
            [],
        ]
    )
    table = _table_frame(result, has_actuals)
    writer.writerow(table.columns)
    for row in table.itertuples(index=False):
        writer.writerow([f"{v:.2f}" if isinstance(v, float) else v for v in row])
    return buffer.getvalue()


def _money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def budget_to_pdf(
    income: float,
    method: str,
    result: pd.DataFrame,
    has_actuals: bool,
    gross_income: float | None = None,
    tax_rate_pct: float | None = None,
) -> bytes:
    """A one-document PDF summary of the plan (and actuals, when a statement was uploaded)."""
    styles = getSampleStyleSheet()
    total_budgeted = float(result["Budgeted Amount"].sum())
    unallocated = income - total_budgeted

    summary = Table(
        [
            *(
                [["Monthly income", _money(income)]]
                if gross_income is None
                else [
                    ["Gross monthly income", _money(gross_income)],
                    ["Estimated tax rate", f"{tax_rate_pct or 0:g}%"],
                    ["Net monthly income", _money(income)],
                ]
            ),
            ["Budgeting method", method],
            ["Total budgeted", _money(total_budgeted)],
            ["Unallocated", _money(unallocated)],
        ],
        colWidths=[2 * inch, 2 * inch],
        hAlign="LEFT",
    )
    summary.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6b7489")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    frame = _table_frame(result, has_actuals)
    header = [c.replace(" Amount", "") for c in frame.columns]
    body = []
    for row in frame.itertuples(index=False):
        body.append([_money(v) if isinstance(v, float) else v for v in row])
    detail = Table([header, *body], repeatRows=1, hAlign="LEFT")
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b5bdb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fb")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6e9f2")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if has_actuals:
        status_color = {"Over": "#b42323", "Near": "#a96500", "Under": "#12805a"}
        for i, status in enumerate(frame["Status"], start=1):
            if status in status_color:
                detail_style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.HexColor(status_color[status])))
    detail.setStyle(TableStyle(detail_style))

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=letter, title="Budget Summary").build(
        [
            Paragraph("Budget Summary", styles["Title"]),
            Paragraph(date.today().strftime("%B %d, %Y"), styles["Normal"]),
            Spacer(1, 14),
            summary,
            Spacer(1, 18),
            Paragraph("Budget vs. Actual" if has_actuals else "Allocations", styles["Heading2"]),
            detail,
        ]
    )
    return buffer.getvalue()
