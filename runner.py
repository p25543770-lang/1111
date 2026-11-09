"""Движок автоматизации: открывает сайт и вводит данные из списка в нужные поля.

Работает на Playwright. Запускается в отдельном потоке, чтобы интерфейс
не подвисал, и отдаёт события (журнал/прогресс) подписчикам через SSE.
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "AutoVvod"
    return Path(__file__).resolve().parent


BASE_DIR = _data_dir()
SHOTS_DIR = BASE_DIR / "screenshots"

DEFAULT_TIMEOUT = 15000


class Stopped(Exception):
    """Пользователь нажал «Стоп»."""


def launch_options(headless: bool, slow_mo: int = 0) -> dict[str, Any]:
    """Параметры запуска браузера (общие для запуска и выбора элементов)."""
    opts: dict[str, Any] = {"headless": headless, "slow_mo": int(slow_mo or 0), "args": []}
    exe = os.environ.get("AUTOFORM_CHROMIUM")
    if exe and Path(exe).exists():
        opts["executable_path"] = exe
    if os.environ.get("AUTOFORM_NO_SANDBOX") or (
        hasattr(os, "geteuid") and os.geteuid() == 0
    ):
        opts["args"].append("--no-sandbox")
    return opts


def value_for(step: dict[str, Any], headers: list[str], row: list[str]) -> str:
    """Значение для шага: из колонки таблицы, по шаблону {{колонка}} или фиксированное."""
    mode = step.get("value_mode") or "fixed"
    if mode == "column":
        col = step.get("column") or ""
        if col in headers:
            return str(row[headers.index(col)])
        return ""
    if mode == "template":
        def repl(match: re.Match[str]) -> str:
            name = match.group(1).strip()
            return str(row[headers.index(name)]) if name in headers else ""

        return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", repl, step.get("value") or "")
    return step.get("value") or ""


class Runner:
    """Один поток = один запуск. Повторный запуск возможен после остановки."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._history: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = "idle"
        self.done = 0
        self.total = 0

    # ---------- события ----------

    def emit(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        with self._lock:
            self._history.append(event)
            if len(self._history) > 4000:
                del self._history[:2000]
            subs = list(self._subs)
        for q in subs:
            q.put(event)

    def log(self, msg: str, level: str = "info") -> None:
        self.emit({"type": "log", "level": level, "msg": msg})

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.emit({"type": "state", "state": state})

    # ---------- управление ----------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, config: dict[str, Any], headers: list[str], rows: list[list[str]]) -> None:
        if self.running:
            raise RuntimeError("Автоматизация уже запущена")
        self._stop.clear()
        self.done = 0
        self.total = max(0, len(rows) - int(config.get("start_row") or 0))
        self._history.clear()
        self._thread = threading.Thread(
            target=self._run, args=(config, headers, rows), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- сам запуск ----------

    def _run(self, config: dict[str, Any], headers: list[str], rows: list[list[str]]) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.log(
                "Не установлен Playwright. Выполните: pip install -r requirements.txt "
                "и затем playwright install chromium",
                "error",
            )
            self._set_state("error")
            return

        url = (config.get("url") or "").strip()
        if not url:
            self.log("Не указан адрес сайта", "error")
            self._set_state("error")
            return

        headless = bool(config.get("headless", True))
        timeout = int(config.get("timeout_ms") or DEFAULT_TIMEOUT)
        wait_after = int(config.get("wait_after_row_ms") or 0) / 1000
        start_row = int(config.get("start_row") or 0)
        repeat = max(1, int(config.get("repeat_count") or 1))
        reload_each = bool(config.get("reload_each_row", False))
        stop_on_error = bool(config.get("stop_on_error", True))
        shoot = bool(config.get("screenshot_on_error", True))
        retry_until = bool(config.get("retry_until_change", False))
        retry_max = max(1, min(50, int(config.get("retry_max") or 5)))
        retry_wait = max(0.1, int(config.get("retry_wait_ms") or 1500) / 1000)
        retry_stop = bool(config.get("retry_stop_on_change", True))
        retry_selector = (config.get("retry_selector") or "").strip()
        steps = config.get("steps") or []
        pre_steps = config.get("pre_steps") or []

        if not steps:
            self.log("Не настроено ни одного шага ввода", "error")
            self._set_state("error")
            return

        self._set_state("running")
        self.log(f"Старт. Строк к обработке: {self.total}")
        self.log(f"Браузер: {'без окна (headless)' if headless else 'с окном'}")
        if retry_until:
            self.log(
                f"Повтор до изменения: до {retry_max} попыток, ожидание {int(retry_wait * 1000)} мс, "
                f"сравнение: {retry_selector or 'вся страница'}"
            )

        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(
                        **launch_options(headless, config.get("slow_mo") or 0)
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log(
                        "Не удалось запустить браузер. Установите его командой: "
                        "playwright install chromium",
                        "error",
                    )
                    raise RuntimeError(str(exc).splitlines()[0][:300]) from exc
                context = browser.new_context(viewport={"width": 1366, "height": 900})
                page = context.new_page()
                page.set_default_timeout(timeout)

                self.log(f"Открываю {url}")
                page.goto(url, wait_until="domcontentloaded")
                self.log(f"Открыто: {page.title() or url}", "ok")

                if pre_steps:
                    self.log("Выполняю подготовительные шаги (до первой строки)")
                    self._exec_steps(page, pre_steps, headers, [], timeout, context)

                if retry_until and retry_selector:
                    try:
                        found = page.locator(retry_selector).count()
                        self.log(
                            f"Элемент для сравнения {retry_selector}: найдено {found}",
                            "ok" if found else "warn",
                        )
                    except Exception:  # noqa: BLE001
                        self.log("Не удалось проверить элемент для сравнения", "warn")

                processed = 0
                failed = 0
                stop_after_change = False
                last_num = 0

                for idx, row in enumerate(rows):
                    if idx < start_row:
                        continue
                    if self._stop.is_set():
                        raise Stopped()

                    num = idx + 1
                    last_num = num
                    self.emit({"type": "progress", "done": processed, "total": self.total, "row": num})
                    brief = self._brief(headers, row)
                    self.log(f"Строка {num}/{len(rows)}: {brief}")

                    attempts = retry_max if retry_until else repeat
                    changed_here = False

                    for rep in range(attempts):
                        if self._stop.is_set():
                            raise Stopped()
                        if reload_each:
                            page.goto(url, wait_until="domcontentloaded")

                        before = self._fingerprint(page, retry_selector) if retry_until else None

                        try:
                            self._exec_steps(page, steps, headers, row, timeout, context)
                        except Stopped:
                            raise
                        except Exception as exc:  # noqa: BLE001 — нужен любой сбой шага
                            failed += 1
                            self.log(f"  ошибка: {self._short(exc)}", "error")
                            if shoot:
                                self._screenshot(page, num)
                            if stop_on_error:
                                raise
                            break

                        if retry_until:
                            # ждём, пока страница отреагирует (анимации, запросы)
                            self._sleep(retry_wait)
                            after = self._fingerprint(page, retry_selector)
                            if before != after:
                                processed += 1
                                self.done = processed
                                changed_here = True
                                self.log(f"  страница изменилась на попытке {rep + 1} — готово", "ok")
                                if not retry_stop:
                                    self.log("  продолжаю со следующей строкой", "ok")
                                break
                            if rep + 1 < attempts:
                                self.log(f"  изменений нет — повторяю ({rep + 2}/{attempts})", "warn")
                            else:
                                processed += 1
                                self.done = processed
                                self.log(f"  изменений нет за {attempts} попыток", "warn")
                                if shoot:
                                    self._screenshot(page, num)
                        else:
                            processed += 1
                            self.done = processed
                            if repeat > 1:
                                self.log(f"  повтор {rep + 1}/{repeat} — ок", "ok")
                            else:
                                self.log("  готово", "ok")
                            time.sleep(max(0, wait_after))

                    if changed_here and retry_stop:
                        stop_after_change = True
                        break

                self.emit({"type": "progress", "done": processed, "total": self.total, "row": last_num or len(rows)})
                if stop_after_change:
                    self.log("Готово: страница изменилась — выполнение остановлено", "ok")
                    self._set_state("done")
                    browser.close()
                    return
                if failed:
                    self.log(f"Готово с ошибками: успешно {processed}, сбоев {failed}", "warn")
                    self._set_state("done")
                else:
                    self.log(f"Готово. Обработано строк: {processed}", "ok")
                    self._set_state("done")
                browser.close()
        except Stopped:
            self.log("Остановлено пользователем", "warn")
            self._set_state("stopped")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Запуск прерван: {self._short(exc)}", "error")
            self._set_state("error")

    def _exec_steps(
        self,
        page: Any,
        steps: list[dict[str, Any]],
        headers: list[str],
        row: list[str],
        timeout: int,
        context: Any,
    ) -> None:
        for i, step in enumerate(steps, 1):
            if self._stop.is_set():
                raise Stopped()
            action = step.get("action") or "fill"
            selector = (step.get("selector") or "").strip()
            value = value_for(step, headers, row)
            delay = int(step.get("delay_ms") or 0)
            optional = bool(step.get("optional"))
            title = step.get("title") or f"Шаг {i}"

            if action == "wait":
                ms = int(value or step.get("delay_ms") or 1000)
                self.log(f"  {title}: пауза {ms} мс")
                self._sleep(ms / 1000)
                continue

            if action == "goto":
                target = value or selector
                self.log(f"  {title}: переход на {target}")
                page.goto(target, wait_until="domcontentloaded")
                continue

            if not selector:
                if optional:
                    continue
                raise RuntimeError(f"{title}: не задан селектор")

            locator = page.locator(selector).first

            try:
                if action == "wait_for":
                    self.log(f"  {title}: ждём элемент {selector}")
                    locator.wait_for(state="visible", timeout=timeout)
                elif action == "fill":
                    locator.wait_for(state="visible", timeout=timeout)
                    locator.fill(value)
                    self.log(f"  {title}: «{self._preview(value)}» → {selector}")
                elif action == "type":
                    locator.wait_for(state="visible", timeout=timeout)
                    locator.click()
                    locator.type(value, delay=40)
                    self.log(f"  {title}: напечатано «{self._preview(value)}»")
                elif action == "click":
                    locator.click(timeout=timeout)
                    self.log(f"  {title}: клик по {selector}")
                elif action == "select":
                    locator.wait_for(state="visible", timeout=timeout)
                    try:
                        locator.select_option(value=value, timeout=timeout)
                    except Exception:
                        locator.select_option(label=value, timeout=timeout)
                    self.log(f"  {title}: выбрано «{self._preview(value)}»")
                elif action == "check":
                    locator.check(timeout=timeout)
                    self.log(f"  {title}: галочка поставлена")
                elif action == "uncheck":
                    locator.uncheck(timeout=timeout)
                    self.log(f"  {title}: галочка снята")
                elif action == "press":
                    locator.press(value or "Enter", timeout=timeout)
                    self.log(f"  {title}: нажата клавиша {value or 'Enter'}")
                elif action == "upload":
                    path = self._resolve_path(value)
                    locator.set_input_files(path, timeout=timeout)
                    self.log(f"  {title}: загружен файл {path}")
                else:
                    raise RuntimeError(f"{title}: неизвестное действие «{action}»")
            except Stopped:
                raise
            except Exception:
                if optional:
                    self.log(f"  {title}: элемент не найден, пропускаю (необязательный)", "warn")
                    continue
                raise

            if delay:
                self._sleep(delay / 1000)

    # ---------- мелочи ----------

    @staticmethod
    def _fingerprint(page: Any, selector: str) -> str:
        """Отпечаток страницы: URL + текст (всей страницы или выбранного элемента)."""
        try:
            if selector:
                text = page.locator(selector).first.inner_text(timeout=3000)
            else:
                text = page.evaluate("() => (document.body ? document.body.innerText : '')")
            raw = f"{page.url or ''}\n{text}"
            return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()
        except Exception:  # noqa: BLE001
            return ""

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self._stop.wait(seconds):
            raise Stopped()

    def _screenshot(self, page: Any, row_num: int) -> None:
        try:
            SHOTS_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = SHOTS_DIR / f"row{row_num}-{stamp}.png"
            page.screenshot(path=str(path), full_page=True)
            self.log(f"  снимок экрана: screenshots/{path.name}", "warn")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _resolve_path(value: str) -> str:
        p = Path(value)
        if not p.is_absolute():
            p = BASE_DIR / p
        return str(p)

    @staticmethod
    def _brief(headers: list[str], row: list[str]) -> str:
        if headers:
            pairs = []
            for i, h in enumerate(headers[:3]):
                if i < len(row) and str(row[i]).strip():
                    pairs.append(f"{h}={row[i]}")
            if pairs:
                return " · ".join(pairs)
        return ", ".join(str(x) for x in row[:3] if str(x).strip()) or "(пустая строка)"

    @staticmethod
    def _preview(value: str, limit: int = 40) -> str:
        value = str(value).replace("\n", " ")
        return value if len(value) <= limit else value[: limit - 1] + "…"

    @staticmethod
    def _short(exc: Exception) -> str:
        text = str(exc).strip().splitlines()
        first = text[0] if text else exc.__class__.__name__
        return first[:400]


runner = Runner()
