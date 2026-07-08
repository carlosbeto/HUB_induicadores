@echo off
setlocal EnableExtensions DisableDelayedExpansion

title Atualizar Tudo e Abrir HUB
C:\Windows\System32\chcp.com 65001 >nul

REM ==========================================================
REM 1) IR PARA A RAIZ DO PROJETO
REM    O .bat está dentro da pasta \bin, então subimos 1 nível.
REM ==========================================================
cd /d "%~dp0.." || exit /b 10

REM ==========================================================
REM 2) DEFINIR PYTHON DA VENV LOCAL DA MAQUINA
REM    IMPORTANTE:
REM    Aqui NAO usamos .venv da rede.
REM    Cada maquina usa seu proprio ambiente virtual local.
REM ==========================================================
set "PY=C:\venv\HUB_indicadores\Scripts\python.exe"

REM ==========================================================
REM 3) VARIAVEIS DO PROJETO
REM ==========================================================
set PYTHONUTF8=1
set "CFG=%CD%\config.json"
set "APP=%CD%\code\hub\app.py"
set "PYTHONPATH=%CD%\code"

REM ==========================================================
REM 4) PASTA DE LOG
REM ==========================================================
set "LOGDIR=%CD%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

for /f %%i in ('C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"
set "LOG=%LOGDIR%\atualizar_e_abrir_hub_%TS%.log"

echo ================================================================
echo ROTINA OFICIAL COMPLETA
echo HUB       : %CD%
echo Python    : %PY%
echo Config    : %CFG%
echo App       : %APP%
echo Log       : %LOG%
echo ================================================================
echo.

REM ==========================================================
REM 5) VALIDACOES INICIAIS
REM ==========================================================
if not exist "%PY%" (
  echo ERRO: Python da venv local nao encontrado:
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

REM ==========================================================
REM 6) ETL - ESTOQUES
REM ==========================================================
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
REM ==========================================================
REM 7) ETL - INVENTARIOS
REM ==========================================================
echo [2/3] Atualizando Inventarios...
call "%PY%" "code\analiseInventarios\scripts\etl_inventarios.py" >> "%LOG%" 2>&1
if errorlevel 1 goto :erro_inventarios

echo.
REM ==========================================================
REM 8) ABRIR HUB
REM    Se ja existir algo ouvindo a porta 8502, nao abre outra vez.
REM ==========================================================
echo [3/3] Abrindo HUB...
echo.

set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8502" ^| findstr "LISTENING"') do set "PORT_PID=%%a"

if defined PORT_PID (
  echo Os dados foram atualizados com sucesso.
  echo O HUB ja esta em execucao na porta 8502.
  echo Atualize a pagina do navegador com F5 para refletir os novos dados.
  echo.
  pause
  exit /b 0
)

call "%PY%" -m streamlit run "%APP%" --server.port 8502

echo.
echo HUB encerrado.
pause
exit /b %ERRORLEVEL%

:erro_estoques
echo.
echo ================================================================
echo ERRO na atualizacao de Estoques
echo Codigo de retorno: %EC%
echo Verifique o log:
echo %LOG%
echo ================================================================
echo.
pause
exit /b %EC%

:erro_inventarios
set "EC=%ERRORLEVEL%"
echo.
echo ================================================================
echo ERRO na atualizacao de Inventarios
echo Codigo de retorno: %EC%
echo Verifique o log:
echo %LOG%
echo ================================================================
echo.
pause
exit /b %EC%