"""Finance Hub — Spending, Budget and Stocks in one Shiny for Python app.

Run with ``uv run shiny run FinanceHub/app.py``. A single uploaded credit-card
statement is shared by Spending and Budget (categories fixed once apply to
both), and Home summarizes all three. The Stocks section is the same module
the standalone ``Stocks/app.py`` mounts.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # project root, so `CreditCard.*`, `Budget.*`, `Stocks.*` import

from shiny import App, ui  # noqa: E402

from FinanceHub.budget import budget_ui, budget_server  # noqa: E402
from FinanceHub.home import home_server, home_ui  # noqa: E402
from FinanceHub.shared import StatementState, icon  # noqa: E402
from FinanceHub.spending import spending_server, spending_ui  # noqa: E402
from Stocks.studio import HEAD as STOCKS_HEAD, stocks_server, stocks_ui  # noqa: E402

BRAND = ui.div(
    ui.span(icon("bank2"), class_="brand-mark"),
    ui.div("Finance Hub", ui.span("Spend · Budget · Invest", class_="brand-tagline"), class_="brand-text"),
    class_="brand",
)

app_ui = ui.page_navbar(
    ui.nav_panel(ui.span(icon("house", "me-1"), "Home"), home_ui("home"), value="home"),
    ui.nav_panel(ui.span(icon("credit-card", "me-1"), "Spending"), spending_ui("spending"), value="spending"),
    ui.nav_panel(ui.span(icon("wallet2", "me-1"), "Budget"), budget_ui("budget"), value="budget"),
    ui.nav_panel(ui.span(icon("graph-up-arrow", "me-1"), "Stocks"), stocks_ui("stocks"), value="stocks"),
    title=BRAND,
    window_title="Finance Hub",
    header=[*STOCKS_HEAD, ui.include_css(HERE / "hub.css")],
    footer=ui.div(
        icon("info-circle", "me-1"),
        "Personal finance tools for education only — not financial or investment advice. "
        "Statement data stays in this session; stock data comes from Yahoo Finance.",
        class_="app-footer",
    ),
    id="hub",
    fillable=False,
)


def server(input, output, session):
    state = StatementState()
    home_server("home", state)
    spending_server("spending", state)
    budget_server("budget", state)
    stocks_server("stocks")


app = App(app_ui, server)
