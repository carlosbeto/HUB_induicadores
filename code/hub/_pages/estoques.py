# code/hub/pages/estoques.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import streamlit as st


def render(cfg: dict) -> None:
    """
    Página do HUB -> Análise Estoques
    Chama o app do projeto analiseEstoques (que já está funcionando).
    """
    # import lazy (só em runtime)
    from analiseEstoques.scripts import streamlit_app as estoques_app

    # roda a UI do projeto
    estoques_app.run()