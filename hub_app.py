# hub_app.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import streamlit as st

HUB_ROOT = Path(__file__).resolve().parent

P_ESTOQUES = HUB_ROOT / "code" / "analiseEstoques" / "scripts" / "streamlit_app.py"
P_INVENT  = HUB_ROOT / "code" / "analiseInventarios" / "scripts" / "streamlit_app.py"

st.set_page_config(page_title="HUB Indicadores", layout="wide")
st.title("HUB Indicadores")
st.caption("Escolha uma ferramenta no menu à esquerda.")

pages = []

if P_ESTOQUES.exists():
    pages.append(
        st.Page(
            str(P_ESTOQUES),
            title="Análise de Estoques (MAST)",
            icon="📦",
            url_path="estoques",   # <<< ISSO resolve o conflito
        )
    )
else:
    st.error(f"Página de Estoques não encontrada: {P_ESTOQUES}")

if P_INVENT.exists():
    pages.append(
        st.Page(
            str(P_INVENT),
            title="Análise de Inventários",
            icon="🧾",
            url_path="inventarios",  # <<< único também
        )
    )
else:
    st.warning(f"Página de Inventários ainda não encontrada: {P_INVENT}")

if not pages:
    st.stop()

pg = st.navigation(pages, position="sidebar")
pg.run()