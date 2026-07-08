# code/hub/pages/inventarios.py
# -*- coding: utf-8 -*-

from __future__ import annotations


def render(cfg: dict) -> None:
    """
    Página do HUB -> Análise Inventários
    """
    from analiseInventarios.scripts.streamlit_entry import render as render_inventarios

    render_inventarios(cfg)