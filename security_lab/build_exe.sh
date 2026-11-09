#!/usr/bin/env bash
# Сборка одного исполняемого файла (Linux/macOS) — то же, что build_exe.bat для Windows.
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/2] Ставлю PyInstaller..."
python3 -m pip install --quiet --upgrade pyinstaller

echo "[2/2] Собираю бинарник..."
python3 -m PyInstaller --noconfirm --clean --onefile --console \
    --name PasswordLab \
    --add-data "wordlist.txt:." \
    lab_app.py

echo
echo "Готово: dist/PasswordLab  ($(du -h dist/PasswordLab | cut -f1))"
echo "Windows-версию собирайте на Windows через build_exe.bat —"
echo "кросс-компиляция .exe из Linux без Wine невозможна."
