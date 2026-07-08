@echo off
setlocal EnableExtensions DisableDelayedExpansion

title Atualizar Tudo e Abrir HUB
C:\Windows\System32\chcp.com 65001 >nul

cd /d "%~dp0.." || exit /b 10

set "PY=%CD%\.venv\Scripts\python.exe"
set PYTHONUTF8=1
set "CFG=%CD%\config.json"
set "APP=%CD%\code\hub\app.py"
set "PYTHONPATH=%CD%\code"

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
if errorlevel 1 goto :erro_inventarios

echo.
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

set "PYTHONPATH=%CD%\code"
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