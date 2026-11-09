#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
target_demo.py — маленький учебный сайт с формой пароля на стандартной библиотеке.

Две формы с одинаковым паролем, но разной защитой:
  http://localhost:8000/          — без защиты (перебор идёт на полной скорости)
  http://localhost:8000/protected — задержка ответа + блокировка после 5 ошибок

Пароль задаётся переменной PASSWORD (по умолчанию "sunshine" — есть в словаре).
Запуск:  python3 target_demo.py
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import time
import urllib.parse

PASSWORD = "sunshine"
MAX_FAILS = 5
LOCK_SECONDS = 60
RESPONSE_DELAY = 0.4      # искусственная задержка защищённой формы

fails = {}
locked_until = {}


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Вход для администратора</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#eef0f6;
      font-family:'Segoe UI',system-ui,sans-serif;color:#1b2340}}
 .card{{background:#fff;border-radius:18px;padding:26px 28px;width:420px;
       box-shadow:0 12px 40px rgba(30,40,90,.10)}}
 h1{{font-size:20px;margin:0 0 18px}}
 .err{{background:#fdecec;border:1px solid #f3c1c1;color:#b42323;border-radius:10px;
      padding:12px 14px;margin-bottom:16px;font-size:15px}}
 .ok{{background:#e9f9ef;border:1px solid #b7e6c8;color:#137a3a;border-radius:10px;
      padding:12px 14px;margin-bottom:16px;font-size:15px}}
 label{{display:block;font-size:14px;color:#5a6480;margin-bottom:6px}}
 input{{width:100%;box-sizing:border-box;padding:13px 14px;font-size:15px;
       border:1px solid #d7dbe7;border-radius:10px;margin-bottom:16px;outline:none}}
 input:focus{{border-color:#5b4bdd;box-shadow:0 0 0 3px rgba(91,75,221,.12)}}
 button{{width:100%;padding:14px;font-size:16px;font-weight:600;color:#fff;
        background:#4b39e0;border:0;border-radius:10px;cursor:pointer}}
</style></head>
<body><div class="card">
<h1>Вход для администратора</h1>
{msg}
<form method="post" action="{action}">
  <label for="password">Пароль</label>
  <input id="password" name="password" type="password" placeholder="Пароль администратора" autofocus>
  <button type="submit">Войти</button>
</form>
</div></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # тише в консоли
        pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        action = "/protected" if path == "/protected" else "/"
        self._send(200, PAGE.format(msg="", action=action))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        protected = path == "/protected"
        ip = self.client_address[0]

        length = int(self.headers.get("Content-Length") or 0)
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        password = (data.get("password") or [""])[0]

        if protected:
            now = time.time()
            if locked_until.get(ip, 0) > now:
                left = int(locked_until[ip] - now)
                self._send(429, PAGE.format(
                    msg=f'<div class="err">Слишком много попыток. Повторите через {left} с.</div>',
                    action="/protected"))
                return
            time.sleep(RESPONSE_DELAY)

        if password == PASSWORD:
            fails[ip] = 0
            self._send(200, PAGE.format(
                msg='<div class="ok">Вы вошли как администратор.</div>', action=path))
            return

        if protected:
            fails[ip] = fails.get(ip, 0) + 1
            if fails[ip] >= MAX_FAILS:
                locked_until[ip] = time.time() + LOCK_SECONDS
                fails[ip] = 0
                self._send(429, PAGE.format(
                    msg=f'<div class="err">Слишком много попыток. Вход заблокирован на {LOCK_SECONDS} с.</div>',
                    action="/protected"))
                return

        self._send(200, PAGE.format(msg='<div class="err">Неверный пароль</div>', action=path))

    def _send(self, code, body):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Учебный сайт для опытов с подбором пароля.")
    ap.add_argument("--password", default=PASSWORD, help="пароль администратора")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 — доступен и с телефона в той же сети")
    a = ap.parse_args()
    PASSWORD = a.password
    print(f"Учебный сайт: http://localhost:{a.port}/  (пароль: {PASSWORD!r})")
    print(f"Защищённая версия: http://localhost:{a.port}/protected")
    if a.host == "0.0.0.0":
        print("Доступен с других устройств в вашей сети по адресу http://<ваш-LAN-IP>:%d/" % a.port)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
