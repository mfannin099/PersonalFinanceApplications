"""State and UI helpers shared by the Finance Hub's sections."""

import plotly.graph_objects as go
import pandas as pd
from shiny import reactive, ui

from CreditCard.categorize import categorize_transactions

PALETTE = ["#3b5bdb", "#eb6834", "#1baf7a", "#ae3ec9", "#f08c00", "#0ca8c4", "#d6336c", "#74b816"]
INK, MUTED, GRID = "#1b2437", "#6b7489", "#eef0f7"


class StatementState:
    """One uploaded statement shared by the Spending and Budget sections.

    ``cleaned`` is the output of ``clean_statement``; ``categorized()`` applies
    the keyword rules plus the saved per-merchant overrides. Saving an override
    calls ``bump()``, which makes every section that reads ``categorized()``
    recompute — that's the "one upload, every view agrees" part of the blend.
    """

    def __init__(self) -> None:
        self.cleaned = reactive.Value(None)
        self.source = reactive.Value("")
        self.version = reactive.Value(0)

        @reactive.calc
        def categorized() -> pd.DataFrame | None:
            self.version()
            df = self.cleaned()
            return None if df is None else categorize_transactions(df)

        self.categorized = categorized

    def load(self, cleaned: pd.DataFrame, source: str) -> None:
        self.cleaned.set(cleaned)
        self.source.set(source)

    def bump(self) -> None:
        self.version.set(self.version.get() + 1)


def icon(name: str, class_: str = "") -> ui.Tag:
    return ui.tags.i(class_=f"bi bi-{name} {class_}".strip())


def card_header(glyph: str, title: str, subtitle: str = "") -> ui.Tag:
    return ui.card_header(
        ui.div(icon(glyph, "me-2 text-muted"), title, class_="fw-semibold"),
        ui.div(subtitle, class_="section-sub") if subtitle else None,
    )


def kpi(title: str, value: str, sub: str = "", glyph: str = "cash", class_: str = "") -> ui.Tag:
    return ui.div(
        ui.div(icon(glyph), class_="metric-icon"),
        ui.div(
            ui.div(title, class_="metric-title"),
            ui.div(value, class_="metric-value"),
            ui.div(sub, class_="metric-sub"),
            class_="metric-body",
        ),
        class_=f"metric-card {class_}".strip(),
    )


def empty_state(glyph: str, title: str, text: str) -> ui.Tag:
    return ui.div(icon(glyph, "empty-icon"), ui.div(title, class_="fw-semibold"), ui.div(text, class_="section-sub"), class_="hub-empty")


def style_figure(fig: go.Figure, height: int = 380, money: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        legend={"orientation": "h", "y": 1.02, "x": 0, "yanchor": "bottom"},
        font={"family": "Inter, sans-serif", "color": INK, "size": 12},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis={"gridcolor": GRID},
        yaxis={"gridcolor": GRID, "tickprefix": "$" if money else "", "tickformat": ",.0f"},
    )
    return fig


def money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"
