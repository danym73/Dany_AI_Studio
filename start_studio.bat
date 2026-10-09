@echo off
title Dany AI Studio - Control Hub
cd /d "%~dp0"

echo ==========================================================
echo       INICIANDO DANY AI STUDIO HUB v2.3
echo ==========================================================
echo.
echo Comprobando entorno Python...

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo Instala Python 3.10 o superior desde https://www.python.org/
    pause
    exit /b 1
)
echo Usando: %PY%
echo Liberando puerto 7877 de posibles procesos huerfanos...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7877 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

%PY% studio_core.py --init >nul

echo Abriendo navegador en http://localhost:7877 ...
start "" cmd /c "timeout /t 3 >nul & start http://localhost:7877"

echo Arrancando servidor de control...
%PY% -u studio_server.py
pause
