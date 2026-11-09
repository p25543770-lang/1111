#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Первый запуск: создаю окружение..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt
  .venv/bin/playwright install chromium
fi
exec .venv/bin/python app.py
