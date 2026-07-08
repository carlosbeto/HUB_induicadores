import socket
from pathlib import Path

import streamlit as st


def render(cfg=None):
    st.set_page_config(page_title="HUB - Estoques", layout="wide")
    st.title("Analise Estoques")

    host = socket.gethostname()
    url = f"http://{host}:8503/"

    st.info("Portal do HUB para Estoques. Este botão abre o app que roda na porta 8503.")

    st.link_button("Abrir Analise Estoques (porta 8503)", url)

    st.caption(f"Link direto: {url}")

    # (Opcional) mostrar se o DB do HUB existe (não impede abrir o app)
    try:
        db_rel = Path("data/analiseEstoques/DB/estoque.sqlite")
        db_abs = (Path(__file__).resolve().parents[3] / db_rel).resolve()
        st.caption(f"DB esperado no HUB: {db_abs}")
        st.caption(f"Existe? {'SIM' if db_abs.exists() else 'NÃO'}")
    except Exception:
        pass