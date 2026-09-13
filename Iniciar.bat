@echo off
title RISA Downloader
cd /d "%~dp0"

rem --- Buscar Python ---
set "PYW="
where pythonw >nul 2>&1 && set "PYW=pythonw"
if not defined PYW (
    py -3 --version >nul 2>&1 && set "PYW=py -3 -w"
)
if not defined PYW (
    where python >nul 2>&1 && set "PYW=python"
)

if not defined PYW (
    echo.
    echo  No se ha encontrado Python en este equipo.
    echo.
    echo  Instalalo desde https://www.python.org/downloads/
    echo  IMPORTANTE: marca la casilla "Add python.exe to PATH" durante la
    echo  instalacion, cierra esta ventana y vuelve a ejecutar Iniciar.bat
    echo.
    pause
    exit /b 1
)

start "" %PYW% "%~dp0descargador.py"
exit /b 0
