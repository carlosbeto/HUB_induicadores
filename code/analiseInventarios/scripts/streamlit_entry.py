# code/analiseInventarios/scripts/streamlit_entry.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import runpy


def render(cfg: dict) -> None:
    """
    Entry-point do projeto analiseInventarios dentro do HUB.

    Executa o streamlit_app.py como script (sempre), evitando o problema de:
    - primeira vez funciona (import executa)
    - segunda vez não (import fica cacheado)
    """
    app_file = Path(__file__).resolve().parent / "streamlit_app.py"
    runpy.run_path(str(app_file), run_name="__main__")