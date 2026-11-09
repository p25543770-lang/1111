#!/usr/bin/env bash
# Запуск приложения с секундомером (Linux/macOS). Аналог START.bat.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 не найден. Установите: https://python.org/downloads"
    exit 1
fi
python3 lab_app.py "$@"
