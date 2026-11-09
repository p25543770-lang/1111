#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guess_password.py — CLI-версия перебора пароля к СВОЕМУ сайту.

Вся логика лежит в lab_core.py, поэтому CLI и приложение (lab_app.py) ведут себя
одинаково. Если нужен секундомер и вопросы «по-человечески» — запускайте lab_app.py.

Примеры:
  python3 guess_password.py --url http://localhost:8000/ --wordlist wordlist.txt
  python3 guess_password.py --url http://localhost:8000/ --charset digits --max-len 4
  python3 guess_password.py --url http://localhost:8000/ --charset alnum --max-len 8 --dry-run
"""

from __future__ import annotations

import argparse
import sys

import lab_core
from lab_core import CHARSETS, Settings, Cracker, clock, human_time


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Учебный подбор пароля к своему сайту (localhost / локальная сеть).",
    )
    p.add_argument("--url", required=True)
    p.add_argument("--method", choices=["post", "get"], default="post")
    p.add_argument("--field", default="password", help="имя поля с паролем")
    p.add_argument("--extra", action="append", default=[], metavar="K=V")
    p.add_argument("--wordlist", help="файл со словарём")
    p.add_argument("--no-dictionary", action="store_true",
                   help="не брать встроенный словарь (только --wordlist / --charset)")
    p.add_argument("--charset", help="полный перебор: " + lab_core.CHARSET_HELP)
    p.add_argument("--min-len", type=int, default=1)
    p.add_argument("--max-len", type=int, default=4)
    p.add_argument("--skip", type=int, default=0, help="пропустить N кандидатов (докачка)")
    p.add_argument("--max-attempts", type=int, default=0)
    p.add_argument("--fail-text", default="Неверный пароль")
    p.add_argument("--success-text")
    p.add_argument("--csrf-field")
    p.add_argument("--csrf-regex", default=r'name="%s"[^>]*value="([^"]+)"')
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="посчитать объём и выйти")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    s = Settings(
        url=a.url, method=a.method, field=a.field, extra=a.extra,
        wordlist=a.wordlist, use_default_wordlist=not a.no_dictionary,
        charset=a.charset, min_len=a.min_len, max_len=a.max_len,
        skip=a.skip, max_attempts=a.max_attempts,
        fail_text=a.fail_text, success_text=a.success_text, verify=not a.no_verify,
        csrf_field=a.csrf_field, csrf_regex=a.csrf_regex,
        delay=a.delay, timeout=a.timeout,
    )

    cracker = Cracker(s)
    ok, reason = cracker.precheck()
    if not ok:
        print(f"Отказ: {reason}.")
        print(lab_core.GUARD_MESSAGE)
        return 2

    total = s.total_candidates()
    cs = s.charset_value
    if cs:
        print(f"Полный перебор: алфавит {len(cs)} симв. ({a.charset}), длина "
              f"{max(1, s.min_len)}..{s.max_len} → "
              f"{lab_core.keyspace_size(cs, s.min_len, s.max_len):,} вариантов".replace(",", " "))
        print("  для сравнения: словарь в 10 000 частых паролей — это 10 000 вариантов")

    if a.dry_run:
        for speed, label in ((10, "медленный сервер с лимитом попыток"),
                             (1000, "быстрый локальный сервер без защиты")):
            print(f"  {label} ({speed} попыток/с): {human_time((total or 0) / speed)}")
        return 0

    print(f"Цель: {s.method.upper()} {s.url}  (поле: {s.field})")
    print("Ctrl+C — остановить.\n")

    last_draw = [0.0]

    def on_progress(info: dict) -> None:
        import time as _t
        now = _t.perf_counter()
        if now - last_draw[0] < 0.1:
            return
        last_draw[0] = now
        line = (f"[{clock(info['elapsed'])}] {info['attempts']} попыток, "
                f"{info['rate']:.0f}/с")
        if info.get("eta"):
            line += f", осталось ~{human_time(info['eta'])}"
        line += f", последняя: {info['last']!r}"
        sys.stdout.write("\r" + line[:110].ljust(110))
        sys.stdout.flush()

    def on_log(msg: str) -> None:
        sys.stdout.write("\r" + " " * 110 + "\r")
        print(f"  {msg}")

    res = cracker.run(on_progress=on_progress, on_log=on_log)
    sys.stdout.write("\r" + " " * 110 + "\r")

    if a.verbose and res.last:
        print(f"последняя попытка: {res.last!r}")

    print("-" * 60)
    print(f"Попыток: {res.attempts}   Секундомер: {clock(res.elapsed)}   "
          f"Скорость: {res.rate:.1f} попыток/с")
    if res.error:
        print(f"Ошибка: {res.error}")
        return 1
    if res.found:
        print(f"ПАРОЛЬ НАЙДЕН: {res.password!r}")
        try:
            with open("found.txt", "w", encoding="utf-8") as f:
                f.write(res.password + "\n")
            print("Записан в found.txt")
        except OSError:
            pass
        return 0
    print("Не найдено.")
    if res.blocked:
        print("Сервер ограничивал попытки — значит защита включена и работает.")
    if a.skip or res.attempts:
        print(f"Продолжить можно с --skip {a.skip + res.attempts}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
