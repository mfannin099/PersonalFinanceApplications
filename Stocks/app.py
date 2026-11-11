"""Stock Studio — standalone Shiny for Python entry point.

Run with ``uv run shiny run Stocks/app.py``. All the UI and logic live in
``Stocks/studio.py`` as a Shiny module (shared with the blended
``FinanceHub`` app); this file just mounts it on its own page.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root, for `Stocks.*` imports

from shiny import App, ui  # noqa: E402

from Stocks.studio import BRAND, HEAD, stocks_server, stocks_ui  # noqa: E402

app_ui = ui.page_navbar(
    ui.nav_panel("Analysis", stocks_ui("studio")),
    title=BRAND,
    window_title="Stock Studio",
    header=HEAD,
    fillable=False,
    id="shell",
)


def server(input, output, session):
    stocks_server("studio")


app = App(app_ui, server)
