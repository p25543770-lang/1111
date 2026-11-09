@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: создаю окружение...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\playwright install chromium
)
.venv\Scripts\python app.py
pause
