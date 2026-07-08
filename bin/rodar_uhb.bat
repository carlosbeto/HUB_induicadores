@echo off
cd /d \\SJO-FILE-03\Filial São José$\Remanufatura\Gestão\xApps\HUB_indicadores

call .venv\Scripts\activate

python -m streamlit run code\hub\app.py --server.port 8502

pause