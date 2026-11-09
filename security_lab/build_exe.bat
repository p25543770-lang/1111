@echo off
chcp 65001 >nul
setlocal
title Сборка PasswordLab.exe
cd /d "%~dp0"

echo ============================================================
echo   Сборка PasswordLab.exe из lab_app.py
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python не найден.
    echo     Установите его с https://python.org  ^(галочка "Add python.exe to PATH"^)
    echo     и запустите этот файл ещё раз.
    pause
    exit /b 1
)

python -c "import sys; print('Python', sys.version.split()[0])"

echo.
echo [1/2] Ставлю PyInstaller (один раз, нужен интернет)...
python -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo [!] Не удалось поставить PyInstaller. Проверьте интернет.
    pause
    exit /b 1
)

echo [2/2] Собираю exe...
python -m PyInstaller --noconfirm --clean --onefile --console ^
    --name PasswordLab ^
    --add-data "wordlist.txt;." ^
    lab_app.py
if errorlevel 1 (
    echo [!] Сборка не удалась. Скопируйте текст ошибки выше.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Готово: dist\PasswordLab.exe
echo   Можно копировать на другой компьютер - Python не нужен.
echo ============================================================
if exist dist\PasswordLab.exe start "" explorer "dist"
pause
