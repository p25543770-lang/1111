#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_core.py — движок учебного подбора пароля. Общая часть для CLI и «приложения».

Зависимостей нет, только стандартная библиотека Python 3.8+.
Работает только со своей машиной и своей локальной сетью (см. is_own_network).
"""

from __future__ import annotations

import ipaddress
import itertools
import re
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field as dc_field
from http.cookiejar import CookieJar

CHARSETS = {
    "digits": string.digits,
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
    "alnum": string.ascii_lowercase + string.digits,
    "alnum-big": string.ascii_letters + string.digits,
    "full": string.ascii_letters + string.digits + string.punctuation,
    "lower-ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "телефон": "0123456789",          # удобный псевдоним
}

CHARSET_HELP = (
    "digits - 10 цифр | lower - a-z (26) | upper - A-Z (26) | "
    "alnum - a-z0-9 (36) | alnum-big - a-zA-Z0-9 (62) | "
    "full - всё печатное (94) | lower-ru - русские буквы"
)

DEFAULT_WORDLIST = [
    "123456", "12345", "123456789", "12345678", "1234567", "password", "password1",
    "Passw0rd", "P@ssw0rd", "qwerty", "qwerty123", "qwerty1", "1q2w3e4r", "1qaz2wsx",
    "zaq12wsx", "qazwsx", "abc123", "letmein", "welcome", "welcome1", "admin",
    "admin123", "adminadmin", "administrator", "root", "toor", "test", "guest",
    "master", "superadmin", "secret", "default", "changeme", "mypassword", "user",
    "login", "iloveyou", "monkey", "dragon", "sunshine", "111111", "000000", "123123",
    "654321", "пароль", "пароль123", "йцукен", "привет", "админ",
]


# --------------------------------------------------------------------- форматирование

def clock(seconds: float) -> str:
    """00:12.345 — секундомер в формате минут, секунд и миллисекунд."""
    if seconds < 0 or seconds != seconds:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    minutes, rest = divmod(total_ms, 60_000)
    secs, ms = divmod(rest, 1000)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


def human_time(seconds: float) -> str:
    if seconds != seconds or seconds == float("inf"):
        return "бесконечно"
    if seconds < 1:
        return f"{seconds:.2f} с"
    units = [(1, "с"), (60, "мин"), (3600, "ч"), (86400, "сут"),
             (86400 * 365, "лет"), (86400 * 365 * 1000, "тыс. лет"),
             (86400 * 365 * 1e6, "млн лет")]
    for size, name in reversed(units):
        if seconds >= size:
            value = seconds / size
            if value < 1000:
                return f"{value:.1f} {name}"
            return f"{value:,.0f} {name}".replace(",", " ")
    return f"{seconds:.0f} с"


def keyspace_size(charset: str, min_len: int, max_len: int) -> int:
    return sum(len(charset) ** n for n in range(max(1, min_len), max_len + 1))


# --------------------------------------------------------------------- проверка адреса

def is_own_network(host: str) -> tuple[bool, str]:
    """Разрешены только своя машина и своя локальная сеть."""
    host = (host or "").strip("[]").lower().split(":")[0]
    if not host:
        return False, "пустой адрес"
    if host == "localhost" or host == "::1" or host.endswith((".local", ".localhost")):
        return True, "локальный хост"
    if host in ("host.docker.internal", "0.0.0.0") or host.endswith((".internal", ".home", ".lan")):
        return True, "локальный хост"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"{host!r} — доменное имя в интернете"
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_private:
        return True, "частный диапазон (192.168/10/172.16-31)"
    if ip.is_link_local:
        return True, "link-local"
    return False, f"{host} — публичный адрес"


GUARD_MESSAGE = (
    "Работаю только со своей машиной и своей локальной сетью:\n"
    "  localhost, 127.0.0.1, ::1, host.docker.internal, *.local,\n"
    "  192.168.x.x, 10.x.x.x, 172.16-31.x.x, 169.254.x.x\n"
    "Подбор пароля к чужому сервису без письменного разрешения — правонарушение.\n"
    "Для легальной практики на чужом коде: PortSwigger Academy, OWASP Juice Shop,\n"
    "HackTheBox, TryHackMe."
)


# --------------------------------------------------------------------- настройки и итог

@dataclass
class Settings:
    url: str = "http://localhost:8000/"
    method: str = "post"
    field: str = "password"
    extra: list[str] = dc_field(default_factory=list)

    wordlist: str | None = None
    use_default_wordlist: bool = True
    charset: str | None = None
    min_len: int = 1
    max_len: int = 4
    skip: int = 0
    max_attempts: int = 0

    fail_text: str = "Неверный пароль"
    success_text: str | None = None
    verify: bool = True

    csrf_field: str | None = None
    csrf_regex: str = r'name="%s"[^>]*value="([^"]+)"'

    delay: float = 0.0
    timeout: float = 10.0

    @property
    def charset_value(self) -> str | None:
        if not self.charset:
            return None
        return CHARSETS.get(self.charset, self.charset)

    def total_candidates(self) -> int | None:
        """Сколько всего кандидатов (без учёта пересечений словаря и перебора)."""
        total = 0
        have = False
        if self.wordlist:
            try:
                with open(self.wordlist, encoding="utf-8", errors="replace") as f:
                    total += sum(1 for line in f if line.strip())
                have = True
            except OSError:
                return None
        elif self.use_default_wordlist:
            total += len(DEFAULT_WORDLIST)
            have = True
        cs = self.charset_value
        if cs:
            total += keyspace_size(cs, self.min_len, self.max_len)
            have = True
        return total if have else None


@dataclass
class Result:
    found: bool = False
    password: str | None = None
    attempts: int = 0
    elapsed: float = 0.0
    blocked: bool = False
    stopped: bool = False
    error: str | None = None
    last: str | None = None

    @property
    def rate(self) -> float:
        return self.attempts / self.elapsed if self.elapsed > 0 else 0.0


# --------------------------------------------------------------------- движок

class Cracker:
    def __init__(self, settings: Settings):
        self.s = settings
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        self._csrf_value: str | None = None
        self._seen: set[str] = set()

    # --- служебное -----------------------------------------------------
    def precheck(self) -> tuple[bool, str]:
        host = urllib.parse.urlparse(self.s.url).hostname or ""
        return is_own_network(host)

    def _candidates(self):
        s = self.s
        cs = s.charset_value
        if s.wordlist:
            with open(s.wordlist, encoding="utf-8", errors="replace") as f:
                for line in f:
                    word = line.strip()
                    if word and word not in self._seen:
                        self._seen.add(word)
                        yield word
        elif s.use_default_wordlist:
            for word in DEFAULT_WORDLIST:
                if word not in self._seen:
                    self._seen.add(word)
                    yield word
        if cs:
            for length in range(max(1, s.min_len), s.max_len + 1):
                for combo in itertools.product(cs, repeat=length):
                    word = "".join(combo)
                    if word not in self._seen:
                        self._seen.add(word)
                        yield word

    def _request(self, password: str):
        s = self.s
        fields: dict[str, str] = {}
        for item in s.extra:
            if "=" in item:
                k, v = item.split("=", 1)
                fields[k] = v
        if s.csrf_field and self._csrf_value is not None:
            fields[s.csrf_field] = self._csrf_value
        fields[s.field] = password

        body = urllib.parse.urlencode(fields).encode("utf-8")
        headers = {
            "User-Agent": "password-lab/1.2 (own site, authorized test)",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": s.url,
        }
        if s.method == "post":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return urllib.request.Request(s.url, data=body, headers=headers, method="POST")
        return urllib.request.Request(s.url + "?" + body.decode(), headers=headers, method="GET")

    def _fetch_csrf(self) -> str:
        s = self.s
        pattern = (s.csrf_regex % re.escape(s.csrf_field)) if "%s" in s.csrf_regex else s.csrf_regex
        with self._opener.open(
                urllib.request.Request(s.url, headers={"User-Agent": "password-lab/1.2"}),
                timeout=s.timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        m = re.search(pattern, page)
        if not m:
            raise RuntimeError(f"CSRF-токен не найден по шаблону {pattern!r}")
        return m.group(1)

    def _is_success(self, status: int, text: str) -> bool:
        if status >= 400:
            return False
        if self.s.success_text:
            return self.s.success_text in text
        return self.s.fail_text not in text

    # --- основной цикл -------------------------------------------------
    def run(self, on_progress=None, should_stop=None, on_log=None) -> Result:
        s = self.s
        res = Result()

        ok, reason = self.precheck()
        if not ok:
            res.error = reason
            return res

        if s.csrf_field:
            try:
                self._csrf_value = self._fetch_csrf()
            except Exception as e:                      # noqa: BLE001
                res.error = f"CSRF: {e}"
                return res

        log = on_log or (lambda msg: None)
        started = time.perf_counter()
        skipped = 0
        blocks = 0
        total = s.total_candidates()

        try:
            for password in self._candidates():
                if should_stop and should_stop():
                    res.stopped = True
                    break
                if skipped < s.skip:
                    skipped += 1
                    continue
                if s.max_attempts and res.attempts >= s.max_attempts:
                    log(f"Достигнут лимит {s.max_attempts} попыток.")
                    break

                res.attempts += 1
                try:
                    with self._opener.open(self._request(password), timeout=s.timeout) as resp:
                        status = resp.status
                        text = resp.read().decode("utf-8", errors="replace")
                except urllib.error.HTTPError as e:
                    status = e.code
                    text = e.read().decode("utf-8", errors="replace")
                except urllib.error.URLError as e:
                    res.error = f"нет соединения: {e.reason}"
                    break

                if status in (429, 403):
                    blocks += 1
                    res.blocked = True
                    log(f"[{status}] сервер ограничивает попытки (подряд: {blocks})")
                    if blocks >= 3:
                        res.stopped = True
                        log("Сервер стабильно блокирует перебор — защита работает.")
                        break
                    time.sleep(max(s.delay, 1.0))
                    continue
                blocks = 0
                res.last = password

                if self._is_success(status, text):
                    if s.verify and not self._verify(password):
                        log(f"ложное срабатывание на {password!r} — продолжаю")
                    else:
                        res.found = True
                        res.password = password
                        break

                if s.delay:
                    time.sleep(s.delay)

                if on_progress:
                    now = time.perf_counter()
                    res.elapsed = now - started
                    done = s.skip + res.attempts
                    eta = None
                    if total and res.rate > 0:
                        eta = max(total - done, 0) / res.rate
                    on_progress({
                        "elapsed": res.elapsed,
                        "attempts": res.attempts,
                        "rate": res.rate,
                        "last": password,
                        "eta": eta,
                        "total": total,
                    })
        finally:
            res.elapsed = time.perf_counter() - started
        return res

    def _verify(self, password: str) -> bool:
        """Контрольный запрос тем же сессионным клиентом: правда ли вошли."""
        try:
            with self._opener.open(self._request(password), timeout=self.s.timeout) as resp:
                return self._is_success(resp.status, resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            return False
        except urllib.error.URLError:
            return False
