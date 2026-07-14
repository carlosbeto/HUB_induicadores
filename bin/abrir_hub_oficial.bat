@echo off
title HUB Indicadores - DEV - Abrir
setlocal EnableExtensions DisableDelayedExpansion

C:\Windows\System32\chcp.com 65001 >nul

cd /d "%~dp0.." || exit /b 10

set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "APP=%ROOT%\code\hub\app.py"
set "PYTHONPATH=%ROOT%\code"
set "PYTHONUTF8=1"
set "PORT=8503"

echo ================================================================
echo HUB INDICADORES - DEV - ABRIR SEM ATUALIZAR
echo ================================================================
echo Pasta HUB : %ROOT%
echo Python    : %PY%
echo App       : %APP%
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
set "PORT_PID="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  set "PORT_PID=%%P"
)

if defined PORT_PID (
  echo Encerrando processo anterior na porta %PORT%: PID %PORT_PID%
  taskkill /PID %PORT_PID% /F >nul 2>&1
  timeout /t 2 /nobreak >nul
)

echo.
echo Iniciando HUB DEV...
echo Acesso local: http://localhost:%PORT%
echo.

"%PY%" -m streamlit run "%APP%" --server.address 0.0.0.0 --server.port %PORT%

set "EC=%ERRORLEVEL%"

echo.
echo HUB DEV encerrado.
pause
exit /b %EC%
