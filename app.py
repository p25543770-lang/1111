"""АвтоВвод — автоматизация ввода данных на сайте по списку.

Запуск:  python app.py
Затем откройте http://127.0.0.1:8765
"""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8765


def main() -> None:
    import server

    url = f"http://{HOST}:{PORT}"
    print("=" * 60)
    print("  АвтоВвод запущен")
    print(f"  Откройте в браузере: {url}")
    print("  Остановить: Ctrl+C")
    print("=" * 60)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(server.app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
