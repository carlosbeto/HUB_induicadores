from __future__ import annotations

import os
import socket
from pathlib import Path

import streamlit as st


def _host() -> str:
    # Preferência: variável do launcher (bat) -> hostname do PC
    return os.environ.get("HUB_HOST") or os.environ.get("COMPUTERNAME") or socket.gethostname()


def _card(title: str, subtitle: str, img_path: Path, url: str) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([1, 2], vertical_alignment="center")

        with c1:
            if img_path.exists():
                st.image(str(img_path), width="stretch")
            else:
                st.caption(f"(sem imagem: {img_path.name})")

        with c2:
            st.subheader(title)
            st.caption(subtitle)
            st.markdown(
                f'<a href="{url}" target="_blank" style="text-decoration:none;">'
                f'<button style="padding:0.5rem 1rem; border-radius:8px; border:1px solid #ddd; cursor:pointer;">Abrir</button>'
                f"</a>",
                unsafe_allow_html=True,
            )


def render(cfg=None) -> None:
    st.set_page_config(page_title="HUB Indicadores", layout="wide")

    host = _host()

    # ajuste aqui se quiser mudar portas no futuro
       # Portas corretas (Estoques=8503 | Inventários=8504)
    url_estoques = f"http://{host}:8503/"
    url_inventarios = f"http://{host}:8504/"

    assets = Path(__file__).resolve().parents[1] / "assets"
    img_estoques = assets / "estoques.png"
    img_invent = assets / "inventarios.png"

    st.title("HUB Indicadores")
    st.caption("Launcher — clique para abrir cada projeto na sua porta.")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        _card(
            "Análise Estoques",
            "MAST / MASR / WEPV — leitura do SQLite do HUB",
            img_estoques,
            url_estoques,
        )

    with col2:
        _card(
            "Análise Inventários",
            "Inventários (MM + EWM) — leitura do SQLite do HUB",
            img_invent,
            url_inventarios,
        )