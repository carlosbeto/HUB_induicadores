@echo off
title HUB Indicadores - DEV - Atualizar e Abrir
setlocal EnableExtensions DisableDelayedExpansion

C:\Windows\System32\chcp.com 65001 >nul

cd /d "%~dp0.." || exit /b 10

set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "CFG=%ROOT%\config.json"
set "APP=%ROOT%\code\hub\app.py"
set "PYTHONPATH=%ROOT%\code"
set "PYTHONUTF8=1"
set "PORT=8503"

set "LOGDIR=%ROOT%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

for /f %%i in ('C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"
set "LOG=%LOGDIR%\dev_atualizar_e_abrir_hub_%TS%.log"

echo ================================================================
echo HUB INDICADORES - ROTINA DEV
echo ================================================================
echo Pasta HUB : %ROOT%
echo Python    : %PY%
echo Config    : %CFG%
echo App       : %APP%
echo Porta     : %PORT%
echo Log       : %LOG%
echo ================================================================
echo.

if not exist "%PY%" (
  echo ERRO: Python da .venv nao encontrado:
  echo %PY%
  echo.
  pause
  exit /b 101
)

if not exist "%CFG%" (
  echo ERRO: config.json nao encontrado:
  echo %CFG%
  echo.
  pause
  exit /b 102
)

if not exist "%APP%" (
  echo ERRO: app.py do HUB nao encontrado:
  echo %APP%
  echo.
  pause
  exit /b 103
)

echo [0/3] Validando ambiente Python...
"%PY%" -c "import streamlit, pandas, pyodbc, dotenv; print('Python e dependencias principais: OK')"
if errorlevel 1 goto :erro_ambiente

echo.
echo [1/3] Atualizando Estoques...
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$env:PYTHONPATH='%PYTHONPATH%';" ^
  "$env:PYTHONUTF8='1';" ^
  "& '%PY%' -u -m analiseEstoques.scripts.update_from_sap --config '%CFG%' 2>&1" ^
  "| ForEach-Object { $_.ToString() }" ^
  "| Tee-Object -FilePath '%LOG%'; exit $LASTEXITCODE"

set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" goto :erro_estoques

echo.
echo [2/3] Atualizando Inventarios...
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$env:PYTHONPATH='%PYTHONPATH%';" ^
  "$env:PYTHONUTF8='1';" ^
  "& '%PY%' -u 'code\analiseInventarios\scripts\etl_inventarios.py' 2>&1" ^
  "| ForEach-Object { $_.ToString() }" ^
  "| Tee-Object -FilePath '%LOG%' -Append; exit $LASTEXITCODE"

set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" goto :erro_inventarios

echo.
echo [3/3] Reiniciando HUB DEV...
echo.

set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  set "PORT_PID=%%P"
)

if defined PORT_PID (
  echo Encerrando processo anterior na porta %PORT%: PID %PORT_PID%
  taskkill /PID %PORT_PID% /F >nul 2>&1
  timeout /t 2 /nobreak >nul
)

echo Atualizacao concluida com sucesso.
echo Iniciando HUB DEV na porta %PORT%...
echo Acesso local: http://localhost:%PORT%
echo.

"%PY%" -m streamlit run "%APP%" --server.address 0.0.0.0 --server.port %PORT%

set "EC=%ERRORLEVEL%"

echo.
echo HUB DEV encerrado.
pause
exit /b %EC%

:erro_ambiente
set "EC=%ERRORLEVEL%"
echo.
echo ================================================================
echo ERRO NA VALIDACAO DO AMBIENTE PYTHON
echo Codigo de retorno: %EC%
echo ================================================================
echo.
pause
exit /b %EC%

:erro_estoques
echo.
echo ================================================================
echo ERRO NA ATUALIZACAO DE ESTOQUES
echo Codigo de retorno: %EC%
echo Log:
echo %LOG%
echo ================================================================
echo.
pause
exit /b %EC%

:erro_inventarios
echo.
echo ================================================================
echo ERRO NA ATUALIZACAO DE INVENTARIOS
echo Codigo de retorno: %EC%
echo Log:
echo %LOG%
echo ================================================================
echo.
pause
exit /b %EC%
