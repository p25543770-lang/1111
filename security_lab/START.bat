@echo off
chcp 65001 >nul
setlocal
title Password Lab
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ============================================================
    echo   Python не найден.
    echo.
    echo   1. Скачайте Python с https://python.org/downloads
    echo   2. При установке поставьте галочку "Add python.exe to PATH"
    echo   3. Запустите START.bat снова
    echo.
    echo   Либо соберите отдельный exe: build_exe.bat
    echo ============================================================
    pause
    exit /b 1
)

python lab_app.py %*
echo.
pause
