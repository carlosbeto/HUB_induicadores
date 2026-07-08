import socket
from pathlib import Path

import streamlit as st


def render(cfg=None):
    st.set_page_config(page_title="HUB - Inventários", layout="wide")
    st.title("Analise Inventários")

    host = socket.gethostname()
    url = f"http://{host}:8504/"

    st.info("Portal do HUB para Inventários. Este botão abre o app que roda na porta 8504.")

    st.link_button("Abrir Analise Inventários (porta 8504)", url)

    st.caption(f"Link direto: {url}")

    # (Opcional) mostrar se o DB do HUB existe (não impede abrir o app)
    try:
        db_rel = Path("data/analiseInventarios/DB/inventarios.sqlite")
        db_abs = (Path(__file__).resolve().parents[3] / db_rel).resolve()
        st.caption(f"DB esperado no HUB: {db_abs}")
        st.caption(f"Existe? {'SIM' if db_abs.exists() else 'NÃO'}")
    except Exception:
        pass