@echo off
title HUB Indicadores

REM ==========================================================
REM 1) IR PARA A RAIZ DO PROJETO A PARTIR DA PASTA DO .BAT
REM    %~dp0 = pasta onde o .bat está salvo
REM    ..\   = sobe para a raiz do projeto
REM ==========================================================
cd /d "%~dp0\.."

REM ==========================================================
REM 2) ATIVAR AMBIENTE VIRTUAL LOCAL DA MAQUINA
REM ==========================================================
call C:\venv\HUB_indicadores\Scripts\activate

REM ==========================================================
REM 3) CONFIGURAR PYTHONPATH CORRETO
REM ==========================================================
set PYTHONPATH=%CD%\code

REM ==========================================================
REM 4) DIAGNOSTICO VISUAL
REM ==========================================================
echo.
echo ==========================================
echo Pasta atual: %CD%
echo PYTHONPATH: %PYTHONPATH%
echo ==========================================
echo.

REM ==========================================================
REM 5) ABRIR O HUB
REM ==========================================================
python -m streamlit run code\hub\app.py --server.port 8502

pause