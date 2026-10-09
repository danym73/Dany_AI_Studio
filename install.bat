@echo off
title Dany AI Studio - Instalador Rapido
cd /d "%~dp0"
echo ==========================================================
echo       DANY AI STUDIO - INSTALADOR Y VERIFICADOR
echo ==========================================================
echo.

echo 1. Comprobando Python...
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] No se detecta Python en tu sistema.
    echo Descargalo gratis desde: https://www.python.org/downloads/
    echo Marca la casilla Add python.exe to PATH durante la instalacion.
    pause
    exit /b 1
)
%PY% --version

echo.
echo 2. Comprobando GPU NVIDIA...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [AVISO] No se detecto GPU NVIDIA o faltan los drivers.
    echo Puedes usar la suite con el Colab Cloud Bridge incluido.
) else (
    echo [OK] GPU NVIDIA detectada.
)

echo.
echo 3. Comprobando FFmpeg ^(masterizacion^)...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [AVISO] FFmpeg no esta en el PATH: la masterizacion no funcionara.
) else (
    echo [OK] FFmpeg detectado.
)

echo.
echo 4. Creando config.json, secrets.json y carpetas de trabajo...
%PY% studio_core.py --init

echo.
echo ==========================================================
echo  INSTALACION COMPLETADA
echo ==========================================================
echo.
echo Edita config.json si tus rutas son distintas.
echo Para enlazar Colab: %PY% studio_core.py --show-colab-secret
echo Para abrir el estudio ejecuta: start_studio.bat
echo.
pause