#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_app.py — приложение с секундомером: перебор пароля к своему сайту.

Двойной клик по exe (или `python lab_app.py`) — приложение спросит настройки
и запустит перебор с живым секундомером внизу экрана.

Работает только с localhost и своей локальной сетью — это вшито в lab_core.py.

Запуск без вопросов, сразу с параметрами:
  python lab_app.py --url http://localhost:8000/ --charset digits --max-len 4
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

import lab_core
from lab_core import CHARSETS, Settings, Cracker, clock, human_time

APP_VERSION = "1.2"


# --------------------------------------------------------------------- консоль

def setup_console() -> None:
    """UTF-8 в консоли Windows + поддержка \\r без мусора."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("Password Lab - подбор пароля (свой сайт)")
        except Exception:                                   # noqa: BLE001
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # noqa: BLE001
        pass


def clear_line() -> None:
    width = 78
    sys.stdout.write("\r" + " " * width + "\r")


def banner() -> None:
    print("=" * 70)
    print(f"  PASSWORD LAB {APP_VERSION} - подбор пароля к СВОЕМУ сайту")
    print("  localhost / своя локальная сеть. Чужие сайты - запрещено.")
    print("=" * 70)


# --------------------------------------------------------------------- вопросы

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return value or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    d = "д" if default else "н"
    value = ask(f"{prompt} (д/н)", d).lower()
    return value.startswith(("д", "y", "1"))


def interactive_settings() -> Settings:
    s = Settings()
    print()
    print("1. Куда стучимся (ваш сайт):")
    s.url = ask("   Адрес формы", s.url)

    import urllib.parse
    ok, reason = lab_core.is_own_network(urllib.parse.urlparse(s.url).hostname or "")
    if not ok:
        print(f"\n   ОТКАЗ: {reason}")
        print("   " + lab_core.GUARD_MESSAGE.replace("\n", "\n   "))
        raise SystemExit(1)

    print("\n2. Как форма отправляет данные (подсказка: F12 - Network - Payload):")
    s.method = ask("   Метод (post/get)", "post").lower()
    s.field = ask("   Имя поля с паролем", "password")
    extra = ask("   Скрытые поля через запятую, например action=login", "")
    s.extra = [x.strip() for x in extra.split(",") if "=" in x]
    s.fail_text = ask("   Текст ошибки при неверном пароле", "Неверный пароль")

    print("\n3. Что перебираем:")
    print("   1 - словарь частых паролей (быстро и реалистично)")
    print("   2 - словарь + ВСЕ пароли по алфавиту")
    mode = ask("   Режим", "1")

    if mode.strip() == "2":
        print("\n   Алфавиты: " + lab_core.CHARSET_HELP)
        s.charset = ask("   Алфавит (можно свою строку, например abc123)", "digits")
        s.min_len = int(ask("   Мин. длина", "1") or 1)
        s.max_len = int(ask("   Макс. длина", "4") or 4)
        cs = CHARSETS.get(s.charset, s.charset)
        total = lab_core.keyspace_size(cs, s.min_len, s.max_len) + len(lab_core.DEFAULT_WORDLIST)
        print(f"\n   Объём: {total:,} вариантов".replace(",", " "))
        print(f"   При 1000 попыток/с это {human_time(total / 1000)}")
        if not ask_yes("   Продолжаем?", True):
            raise SystemExit(0)

    s.delay = float(ask("\n4. Пауза между попытками, сек", "0") or 0)
    return s


# --------------------------------------------------------------------- вывод

def mode_description(s: Settings) -> str:
    parts = []
    if s.wordlist or s.use_default_wordlist:
        parts.append(f"словарь ({'файл' if s.wordlist else str(len(lab_core.DEFAULT_WORDLIST)) + ' паролей'})")
    cs = s.charset_value
    if cs:
        parts.append(f"перебор {s.charset!r} длина {s.min_len}..{s.max_len} "
                     f"({lab_core.keyspace_size(cs, s.min_len, s.max_len):,} вариантов)".replace(",", " "))
    return " + ".join(parts) or "словарь"


def write_report(s: Settings, res: lab_core.Result) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "report.txt")
    lines = [
        "ОТЧЁТ О ПЕРЕБОРЕ ПАРОЛЯ (учебная лаборатория)",
        "=" * 56,
        f"Дата:        {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Адрес:       {s.method.upper()} {s.url}",
        f"Поле:        {s.field}",
        f"Режим:       {mode_description(s)}",
        "-" * 56,
        f"Результат:   {'ПАРОЛЬ НАЙДЕН: ' + repr(res.password) if res.found else 'не найден'}",
        f"Попыток:     {res.attempts}",
        f"Время:       {clock(res.elapsed)} ({res.elapsed:.3f} с)",
        f"Скорость:    {res.rate:.1f} попыток/с",
        f"Блокировки:  {'да, сервер ограничивал попытки' if res.blocked else 'нет'}",
    ]
    if res.error:
        lines.append(f"Ошибка:      {res.error}")
    lines += [
        "=" * 56,
        "Вывод: это число попыток в секунду и есть то, что уменьшает лимит",
        "попыток на сервере. С ним перебор растягивается с секунд на годы.",
    ]
    text = "\n".join(lines) + "\n"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except OSError:
        return ""


def print_result(s: Settings, res: lab_core.Result) -> None:
    print()
    print("=" * 70)
    if res.error:
        print(f"  ОШИБКА: {res.error}")
    elif res.found:
        print("  ПАРОЛЬ НАЙДЕН")
        print(f"  Пароль:      {res.password!r}")
    elif res.blocked:
        print("  НЕ НАЙДЕН - сервер заблокировал перебор (защита работает)")
    elif res.stopped:
        print("  ОСТАНОВЛЕНО")
    else:
        print("  НЕ НАЙДЕН в пределах перебора")
    print("-" * 70)
    print(f"  СЕКУНДОМЕР:  {clock(res.elapsed)}      ({res.elapsed:.3f} с)")
    print(f"  Попыток:     {res.attempts}")
    print(f"  Скорость:    {res.rate:.1f} попыток/с")
    if res.blocked:
        print("  Блокировка:  да, сервер ограничивал попытки")
    print("=" * 70)

    path = write_report(s, res)
    if path:
        print(f"  Отчёт сохранён: {path}")


# --------------------------------------------------------------------- запуск

def main() -> int:
    setup_console()

    p = argparse.ArgumentParser(description="Перебор пароля к своему сайту, с секундомером.")
    p.add_argument("--url")
    p.add_argument("--method", default="post", choices=["post", "get"])
    p.add_argument("--field", default="password")
    p.add_argument("--extra", action="append", default=[])
    p.add_argument("--wordlist")
    p.add_argument("--charset")
    p.add_argument("--min-len", type=int, default=1)
    p.add_argument("--max-len", type=int, default=4)
    p.add_argument("--fail-text", default="Неверный пароль")
    p.add_argument("--success-text")
    p.add_argument("--csrf-field")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--skip", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=0)
    p.add_argument("--no-verify", action="store_true", help="не делать контрольный запрос")
    args = p.parse_args()

    banner()

    if args.url:
        s = Settings(
            url=args.url, method=args.method, field=args.field, extra=args.extra,
            wordlist=args.wordlist, charset=args.charset, min_len=args.min_len,
            max_len=args.max_len, fail_text=args.fail_text, success_text=args.success_text,
            csrf_field=args.csrf_field, delay=args.delay, skip=args.skip,
            max_attempts=args.max_attempts, verify=not args.no_verify,
            use_default_wordlist=not bool(args.wordlist),
        )
    else:
        s = interactive_settings()

    cracker = Cracker(s)
    ok, reason = cracker.precheck()
    if not ok:
        print(f"\nОТКАЗ: {reason}")
        print(lab_core.GUARD_MESSAGE)
        return 2

    # общий объём и предупреждение о времени
    total = s.total_candidates()
    print()
    print(f"Цель:     {s.method.upper()} {s.url}")
    print(f"Поле:     {s.field}")
    print(f"Режим:    {mode_description(s)}")
    if total:
        print(f"Объём:    {total:,} кандидатов".replace(",", " "))
        print(f"Оценка:   при 1000 попыток/с это {human_time(total / 1000)}")
    print("-" * 70)
    print("Секундомер пошёл. Ctrl+C - остановить.\n")

    last_draw = 0.0
    found_box: dict[str, object] = {}

    def on_progress(info: dict) -> None:
        nonlocal last_draw
        now = time.perf_counter()
        if now - last_draw < 0.08:          # ~12 обновлений в секунду, чтобы не мигало
            return
        last_draw = now
        eta = info.get("eta")
        eta_txt = f" | осталось ~{clock(eta)}" if eta else ""
        line = (f"[{clock(info['elapsed'])}]  попыток: {info['attempts']:,}".replace(",", " ")
                + f" | {info['rate']:.0f}/с{eta_txt} | {info['last']!r}")
        sys.stdout.write("\r" + line[:100].ljust(100))
        sys.stdout.flush()

    def on_log(msg: str) -> None:
        clear_line()
        print(f"  {msg}")

    started = time.perf_counter()
    try:
        res = cracker.run(on_progress=on_progress, on_log=on_log)
    except KeyboardInterrupt:
        res = lab_core.Result(stopped=True, elapsed=time.perf_counter() - started)
        clear_line()
        print("  Остановлено пользователем.")
    finally:
        clear_line()

    print_result(s, res)
    return 0 if res.found else 1


if __name__ == "__main__":
    raise SystemExit(main())
