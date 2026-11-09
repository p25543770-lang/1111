"""АвтоВвод — настольное приложение.

Запускает локальный сервер и открывает интерфейс в собственном окне
(pywebview / WebView2). Сайт, который заполняется, показывается внутри
приложения — отдельное окно браузера не открывается.

Если pywebview недоступен (например, запуск из исходников на Linux),
приложение откроется в обычном браузере.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("AUTOVVOD_PORT", "8765"))
URL = f"http://{HOST}:{PORT}"

FROZEN = bool(getattr(sys, "frozen", False))

# В собранном приложении Chromium лежит рядом с playwright, а не в профиле.
if FROZEN:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    directory = Path(base) / "AutoVvod"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "app.log"


def log(message: str) -> None:
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except Exception:  # noqa: BLE001
        pass


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, port)) == 0


def start_server() -> threading.Thread:
    import uvicorn

    import server

    config = uvicorn.Config(server.app, host=HOST, port=PORT, log_level="warning")
    instance = uvicorn.Server(config)

    thread = threading.Thread(target=instance.run, name="autovvod-server", daemon=True)
    thread.start()
    return thread


def wait_ready(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(PORT):
            return True
        time.sleep(0.2)
    return False


def open_window() -> bool:
    """Показывает приложение в собственном окне. True — окно закрыто пользователем."""
    try:
        import webview
    except Exception:  # noqa: BLE001
        log("pywebview недоступен, открываю браузер:\n" + traceback.format_exc())
        return False

    webview.create_window(
        "АвтоВвод — автоматизация ввода",
        URL,
        width=1440,
        height=920,
        min_size=(1024, 700),
        background_color="#050505",
        text_select=True,
    )
    webview.start(debug=False, private_mode=False)
    return True


def main() -> None:
    log("---- запуск ----")
    if port_open(PORT):
        log("порт занят: использую уже запущенный сервер")
        thread = None
    else:
        thread = start_server()
        if not wait_ready():
            log("сервер не успел запуститься")

    try:
        closed = open_window()
    except Exception:  # noqa: BLE001
        log("ошибка окна:\n" + traceback.format_exc())
        closed = False

    if not closed:
        webbrowser.open(URL)
        try:
            while thread is not None and thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    log("---- выход ----")


if __name__ == "__main__":
    main()
