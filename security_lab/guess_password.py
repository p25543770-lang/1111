#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guess_password.py — учебный перебор пароля к СВОЕМУ сайту (localhost / своя локальная сеть).

Два режима, их можно совмещать:
  * словарь   — список самых частых паролей (быстро и реалистично);
  * все пароли — полный перебор по алфавиту от --min-len до --max-len.

Зависимостей нет, только стандартная библиотека Python 3.8+.

Примеры:
  # словарь
  python3 guess_password.py --url http://localhost:8000/ \
      --wordlist wordlist.txt --fail-text "Неверный пароль"

  # ВСЕ пароли: только цифры, длина 1..6  (10^1+...+10^6 = 1 111 110 вариантов)
  python3 guess_password.py --url http://localhost:8000/ \
      --charset digits --min-len 1 --max-len 6 --fail-text "Неверный пароль"

  # все пароли: a-z, длина ровно 3 (17 576 вариантов — быстро)
  python3 guess_password.py --url http://localhost:8000/ \
      --charset lower --min-len 3 --max-len 3 --fail-text "Неверный пароль"

  # сначала словарь, потом всё остальное; продолжить с 20 000-й попытки
  python3 guess_password.py --url http://localhost:8000/ --wordlist wordlist.txt \
      --charset digits --min-len 1 --max-len 4 --skip 20000 --fail-text "Неверный пароль"

  # посмотреть объём перебора и оценку времени, ничего не отправляя
  python3 guess_password.py --url http://localhost:8000/ --charset alnum \
      --min-len 1 --max-len 8 --dry-run
"""

from __future__ import annotations

import argparse
import ipaddress
import itertools
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_WORDLIST = [
    "123456", "12345", "123456789", "12345678", "1234567", "password", "password1",
    "Passw0rd", "P@ssw0rd", "qwerty", "qwerty123", "qwerty1", "1q2w3e4r", "1qaz2wsx",
    "zaq12wsx", "qazwsx", "abc123", "letmein", "welcome", "welcome1", "admin",
    "admin123", "adminadmin", "administrator", "root", "toor", "test", "guest",
    "master", "superadmin", "secret", "default", "changeme", "mypassword", "user",
    "login", "iloveyou", "monkey", "dragon", "sunshine", "111111", "000000", "123123",
    "654321", "пароль", "пароль123", "йцукен", "привет", "админ",
]

CHARSETS = {
    "digits": string.digits,
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
    "alnum": string.ascii_lowercase + string.digits,
    "alnum-big": string.ascii_letters + string.digits,
    "full": string.ascii_letters + string.digits + string.punctuation,
    "lower-ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
}


# --------------------------------------------------------------------------- утилиты

def human_time(seconds: float) -> str:
    """Переводит секунды в человеческое «сколько это длится»."""
    if seconds != seconds or seconds == float("inf"):      # nan / inf
        return "бесконечно"
    units = [(1.0, "с"), (60, "мин"), (3600, "ч"), (86400, "сут"),
             (86400 * 365, "лет"), (86400 * 365 * 1000, "тыс. лет"),
             (86400 * 365 * 1e6, "млн лет")]
    if seconds < 1:
        return f"{seconds:.2f} с"
    for size, name in reversed(units):
        if seconds >= size:
            value = seconds / size
            if value >= 1000 and name in ("лет", "тыс. лет"):
                return f"{value:,.0f} {name}".replace(",", " ")
            return f"{value:,.1f} {name}".replace(",", " ") if value < 1000 else f"{value:,.0f} {name}".replace(",", " ")
    return f"{seconds:.0f} с"


def is_own_network(host: str) -> tuple[bool, str]:
    """Разрешены только своя машина и своя локальная сеть."""
    host = host.strip("[]").lower()
    if not host:
        return False, "пустой адрес"
    if host in ("localhost", "0.0.0.0", "::1", "host.docker.internal") or \
       host.endswith((".local", ".localhost", ".internal", ".home", ".lan")):
        return True, "локальный хост"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"{host!r} — это доменное имя в интернете"
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_private:
        return True, "адрес из частного диапазона (192.168/10/172.16–31)"
    if ip.is_link_local:
        return True, "link-local (автонастройка сети)"
    return False, f"{host} — публичный IP-адрес"


def keyspace_size(charset: str, min_len: int, max_len: int) -> int:
    return sum(len(charset) ** n for n in range(min_len, max_len + 1))


# --------------------------------------------------------------------------- аргументы

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Учебный подбор пароля к своему сайту (localhost / локальная сеть).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", required=True, help="адрес формы, например http://localhost:8000/")
    p.add_argument("--method", choices=["post", "get"], default="post")
    p.add_argument("--field", default="password", help="имя поля с паролем")
    p.add_argument("--extra", action="append", default=[], metavar="K=V",
                   help="доп. поля формы, можно несколько раз")

    p.add_argument("--wordlist", help="файл со словарём (одна строка = один пароль)")
    p.add_argument("--charset",
                   help="полный перебор по алфавиту: готовое имя ("
                        + ", ".join(CHARSETS) + ") или строка символов, например abc123")
    p.add_argument("--min-len", type=int, default=1)
    p.add_argument("--max-len", type=int, default=4)
    p.add_argument("--skip", type=int, default=0, help="пропустить N первых кандидатов (докачка)")
    p.add_argument("--max-attempts", type=int, default=0, help="0 = без ограничения")

    p.add_argument("--fail-text", default="Неверный пароль",
                   help="текст, который появляется ТОЛЬКО при ошибке входа")
    p.add_argument("--success-text", help="текст, который появляется ТОЛЬКО при успехе")

    p.add_argument("--csrf-field", help="имя скрытого поля с токеном (например csrf_token)")
    p.add_argument("--csrf-regex", default=r'name="%s"[^>]*value="([^"]+)"',
                   help="регулярка для вытаскивания токена из HTML (%%s -> имя поля)")

    p.add_argument("--delay", type=float, default=0.0, help="пауза между попытками, сек")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--dry-run", action="store_true", help="посчитать объём и выйти")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


# --------------------------------------------------------------------------- перебор

def resolve_charset(spec: str | None) -> str | None:
    if not spec:
        return None
    return CHARSETS.get(spec, spec)


def candidates(args: argparse.Namespace, charset: str | None):
    seen: set[str] = set()

    def emit(word: str):
        if word and word not in seen:
            seen.add(word)
            return word
        return None

    if args.wordlist:
        with open(args.wordlist, encoding="utf-8", errors="replace") as f:
            for line in f:
                word = emit(line.strip())
                if word:
                    yield word
    elif not charset:
        for word in DEFAULT_WORDLIST:
            word = emit(word)
            if word:
                yield word

    if charset:
        for length in range(max(1, args.min_len), args.max_len + 1):
            for combo in itertools.product(charset, repeat=length):
                word = emit("".join(combo))
                if word:
                    yield word


def build_request(args: argparse.Namespace, password: str,
                  csrf_name: str | None = None, csrf_value: str | None = None):
    fields = {}
    for item in args.extra:
        if "=" not in item:
            sys.exit(f"--extra ожидает K=V, получено: {item!r}")
        k, v = item.split("=", 1)
        fields[k] = v
    if csrf_name and csrf_value is not None:
        fields[csrf_name] = csrf_value
    fields[args.field] = password

    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {
        "User-Agent": "local-lab-password-check/1.1 (own site, authorized test)",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": args.url,
    }
    if args.method == "post":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return urllib.request.Request(args.url, data=body, headers=headers, method="POST")
    return urllib.request.Request(args.url + "?" + body.decode(), headers=headers, method="GET")


def fetch_csrf(opener, args: argparse.Namespace) -> str | None:
    """Забирает свежий токен со страницы формы — как это делает браузер."""
    pattern = args.csrf_regex % re.escape(args.csrf_field) if "%s" in args.csrf_regex \
        else args.csrf_regex
    try:
        with opener.open(urllib.request.Request(args.url, headers={"User-Agent": "local-lab/1.1"}),
                         timeout=args.timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        sys.exit(f"Не смог получить страницу формы: {e}")
    m = re.search(pattern, page)
    if not m:
        sys.exit(f"CSRF-токен не найден по шаблону {pattern!r}. Задайте --csrf-regex вручную.")
    return m.group(1)


def main() -> int:
    args = parse_args()

    parsed = urllib.parse.urlparse(args.url)
    ok, why = is_own_network(parsed.hostname or "")
    if not ok:
        sys.exit(
            f"Отказ: {why}.\n"
            "Инструмент работает только со своей машиной и своей локальной сетью:\n"
            "  localhost, 127.0.0.1, ::1, host.docker.internal, *.local,\n"
            "  192.168.x.x, 10.x.x.x, 172.16–31.x.x, 169.254.x.x\n"
            "Проверка чужого сервиса без письменного разрешения — правонарушение.\n"
            "Легальная практика на чужом коде: PortSwigger Academy, OWASP Juice Shop,\n"
            "HackTheBox, TryHackMe — там атаковать разрешено явно."
        )

    charset = resolve_charset(args.charset)
    if charset is not None and (args.min_len < 1 or args.max_len < args.min_len):
        sys.exit("Проверьте --min-len/--max-len.")

    total = None
    if charset:
        total = keyspace_size(charset, max(1, args.min_len), args.max_len)
        if args.wordlist:
            total += sum(1 for _ in open(args.wordlist, encoding="utf-8", errors="replace"))

    if total is not None:
        print(f"Полный перебор: алфавит {len(charset)} симв., длина "
              f"{max(1, args.min_len)}..{args.max_len} → {total:,} вариантов".replace(",", " "))
        print(f"  для сравнения: словарь в 10 000 частых паролей — это 10 000 вариантов")

    if args.dry_run:
        for speed, label in ((10, "медленный сервер с лимитом попыток"),
                             (1000, "быстрый локальный сервер без защиты")):
            print(f"  {label} ({speed} попыток/с): {human_time((total or 0) / speed)}")
        return 0

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    csrf_value = fetch_csrf(opener, args) if args.csrf_field else None
    if args.csrf_field:
        print(f"CSRF-токен получен ({args.csrf_field}), сессия — как у браузера.")

    print(f"Цель: {args.method.upper()} {args.url}  (поле: {args.field})")
    print("Ctrl+C — остановить.\n")

    attempts = 0
    skipped = 0
    blocks = 0
    found: str | None = None
    started = time.perf_counter()
    last_report = started

    try:
        for password in candidates(args, charset):
            if skipped < args.skip:
                skipped += 1
                continue
            if args.max_attempts and attempts >= args.max_attempts:
                print(f"\nДостигнут лимит {args.max_attempts} попыток.")
                break
            attempts += 1

            try:
                with opener.open(build_request(args, password, args.csrf_field, csrf_value),
                                 timeout=args.timeout) as resp:
                    status = resp.status
                    text = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                status = e.code
                text = e.read().decode("utf-8", errors="replace")
            except urllib.error.URLError as e:
                sys.exit(f"\nНе достучался до {args.url}: {e.reason}")

            if status in (429, 403):
                blocks += 1
                wait = max(args.delay, 1.0)
                print(f"  [{status}] сервер ограничивает попытки — пауза {wait:.1f} с "
                      f"(подряд: {blocks})")
                if blocks >= 3:
                    print("\nСервер стабильно блокирует перебор — защита работает.")
                    break
                time.sleep(wait)
                continue
            blocks = 0

            if args.verbose:
                print(f"  #{attempts:<6} {password!r}")

            success = (args.success_text in text) if args.success_text \
                else (args.fail_text not in text)
            if status < 400 and success:
                found = password
                break

            if args.delay:
                time.sleep(args.delay)

            now = time.perf_counter()
            if now - last_report >= 2.0:
                rate = attempts / max(now - started, 1e-9)
                eta = ""
                if total:
                    done = args.skip + attempts
                    eta = f", осталось ~{human_time((total - done) / rate)}"
                print(f"  ... {attempts} попыток, {rate:.1f}/с{eta}, последняя: {password!r}")
                last_report = now
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")

    elapsed = time.perf_counter() - started
    rate = attempts / elapsed if elapsed else 0.0
    print("\n" + "-" * 60)
    print(f"Попыток: {attempts}   Время: {elapsed:.2f} с   Скорость: {rate:.1f} попыток/с")
    if found is not None:
        print(f"ПАРОЛЬ НАЙДЕН: {found!r}")
        with open("found.txt", "w", encoding="utf-8") as f:
            f.write(found + "\n")
        print("Записан в found.txt")
        print("\nОбратите внимание на скорость. Именно это число защищают лимитом попыток.")
        return 0
    print("Не найдено. Расширьте словарь или диапазон длин.")
    if args.skip:
        print(f"Продолжить можно с --skip {args.skip + attempts}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
