"""Budget export helpers — pure (Dash-free), parallel to ``budgeting.py``.

Both exporters take the same inputs: the plan's ``income`` and ``method``, the
``result`` frame from ``budget_vs_actual`` (one row per budget category), and
``has_actuals`` — whether a statement was uploaded. Without a statement the
export is just the plan (Category/Budgeted); with one it also carries
Actual/Variance/Status.
"""

import csv
import io
from datetime import date

import pandas as pd
from Budget.budgeting import NON_CARD_CATEGORIES
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PLAN_COLUMNS = ["Category", "Budgeted Amount"]
_ACTUAL_COLUMNS = ["Category", "Budgeted Amount", "Actual Amount", "Variance", "Status"]


def _table_frame(result: pd.DataFrame, has_actuals: bool) -> pd.DataFrame:
    if not has_actuals:
        return result[_PLAN_COLUMNS]
    # Non-card categories never appear on a card statement, so an actual/variance
    # for them would be a misleading $0 — leave those cells blank.
    frame = result[_ACTUAL_COLUMNS].astype(object)
    frame.loc[frame["Category"].isin(NON_CARD_CATEGORIES), ["Actual Amount", "Variance", "Status"]] = ""
    return frame


def budget_to_csv(income: float, method: str, result: pd.DataFrame, has_actuals: bool) -> str:
    """CSV text: a small ``Field,Value`` header block, a blank row, then the category table."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    total_budgeted = float(result["Budgeted Amount"].sum())
    writer.writerows(
        [
            ["Monthly Income", f"{income:.2f}"],
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


def budget_to_pdf(income: float, method: str, result: pd.DataFrame, has_actuals: bool) -> bytes:
    """A one-document PDF summary of the plan (and actuals, when a statement was uploaded)."""
    styles = getSampleStyleSheet()
    total_budgeted = float(result["Budgeted Amount"].sum())
    unallocated = income - total_budgeted

    summary = Table(
        [
            ["Monthly income", _money(income)],
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
