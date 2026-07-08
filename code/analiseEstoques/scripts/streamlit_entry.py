# code/analiseEstoques/scripts/streamlit_entry.py
# -*- coding: utf-8 -*-

def render():
    """
    Entry point do app de Estoques quando executado a partir do HUB.

    O módulo streamlit_app.py foi estruturado para renderizar a interface
    durante o import em contexto Streamlit. Por isso, aqui basta importar
    o módulo para disparar a página.
    """
    import analiseEstoques.scripts.streamlit_app  # noqa: F401