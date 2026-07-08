@echo off
title HUB Indicadores - Oficial
setlocal EnableExtensions DisableDelayedExpansion

chcp 65001 >nul

cd /d "%~dp0.." || exit /b 10

set "PY=%CD%\.venv\Scripts\python.exe"
set "APP=%CD%\code\hub\app.py"
set "PYTHONPATH=%CD%\code"
set "PORT=8502"

echo ================================================================
echo HUB       : %CD%
echo Python    : %PY%
echo App       : %APP%
echo PYTHONPATH: %PYTHONPATH%
echo Porta     : %PORT%
echo ================================================================
echo.

if not exist "%PY%" (
  echo ERRO: Python da .venv nao encontrado:
  echo %PY%
  echo.
  pause
  exit /b 101
)

if not exist "%APP%" (
  echo ERRO: app.py do HUB nao encontrado:
  echo %APP%
  echo.
  pause
  exit /b 102
)

echo Verificando porta %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  echo Encerrando processo que ocupa a porta %PORT%: PID %%P
  taskkill /PID %%P /F >nul 2>&1
)

echo.
echo Iniciando HUB...
echo.

"%PY%" -m streamlit run "%APP%" --server.port %PORT%

echo.
echo HUB encerrado.
pause
exit /b %ERRORLEVEL%