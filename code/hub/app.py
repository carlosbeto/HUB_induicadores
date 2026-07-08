from __future__ import annotations

import traceback
import inspect
import streamlit as st

from hub.lib.config import load_config


def _fatal(title: str, exc: Exception) -> None:
    st.error(title)
    st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    st.stop()

def _call_compat(fn, cfg: dict) -> None:
    """
    Chama fn() ou fn(cfg) dependendo da assinatura.
    Resolve o erro: render() takes 0 positional arguments but 1 was given
    """
    sig = inspect.signature(fn)
    params = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(params) == 0:
        fn()
    else:
        fn(cfg)    


def _render_home() -> None:
    st.title("HUB Indicadores")

    st.caption("Selecione o relatório para abrir.")

    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.subheader("📦 Análise Estoques")
        # se você tiver uma imagem local depois, a gente coloca aqui com st.image(...)
        if st.button("Abrir Estoques", use_container_width=True):
            st.session_state["hub_page"] = "estoques"
            st.rerun()

    with c2:
        st.subheader("📋 Análise Inventários")
        if st.button("Abrir Inventários", use_container_width=True):
            st.session_state["hub_page"] = "inventarios"
            st.rerun()

    st.divider()
    st.caption("Dica: para voltar para esta tela, use o botão '🏠 Home' na lateral.")


def _render_estoques(cfg: dict) -> None:
    from hub._pages.estoques import render
    render(cfg)


def _render_inventarios(cfg: dict) -> None:
    from hub._pages.inventarios import render
    render(cfg)


def main() -> None:
    st.set_page_config(page_title="HUB Indicadores", layout="wide")

    # Carrega config.json
    try:
        cfg = load_config()
    except Exception as e:
        _fatal("Falha ao carregar config.json (HUB_CONFIG / caminho / encoding).", e)

    # Estado de navegação
    if "hub_page" not in st.session_state:
        st.session_state["hub_page"] = "home"

    # Sidebar
    st.sidebar.title("Navegação")
    if st.sidebar.button("🏠 Home", use_container_width=True):
        st.session_state["hub_page"] = "home"
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("Abrir direto:")
    if st.sidebar.button("📦 Estoques", use_container_width=True):
        st.session_state["hub_page"] = "estoques"
        st.rerun()
    if st.sidebar.button("📋 Inventários", use_container_width=True):
        st.session_state["hub_page"] = "inventarios"
        st.rerun()

    # Router
    page = st.session_state.get("hub_page", "home")

    try:
        if page == "home":
            _render_home()
        elif page == "estoques":
            _render_estoques(cfg)
        elif page == "inventarios":
            _render_inventarios(cfg)
        else:
            st.session_state["hub_page"] = "home"
            st.rerun()
    except Exception as e:
        _fatal("Erro ao renderizar a página do HUB.", e)


if __name__ == "__main__":
    main()