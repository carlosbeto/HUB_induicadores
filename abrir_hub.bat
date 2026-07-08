@echo off
setlocal EnableExtensions DisableDelayedExpansion

chcp 65001 >nul

REM Vai para a raiz do HUB
cd /d "%~dp0" || exit /b 10

REM Caminhos
set "PY=%CD%\.venv\Scripts\python.exe"
set "APP=%CD%\code\hub\app.py"
set "PYTHONPATH=%CD%\code"

echo ================================================================
echo HUB       : %CD%
echo Python    : %PY%
echo App       : %APP%
echo ================================================================
echo.

if not exist "%PY%" (
  echo ERRO: Python da .venv nao encontrado:
  echo %PY%
  pause
  exit /b 101
)

echo Iniciando HUB...
echo.

"%PY%" -m streamlit run "%APP%" --server.port 8503

echo.
echo HUB encerrado.
pause
exit /b %ERRORLEVEL%