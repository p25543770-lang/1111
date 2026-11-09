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

# Снимок страницы для ИИ-агента: URL, заголовок, видимый текст и интерактивные элементы.
AGENT_SNAPSHOT_JS = r"""
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const textOf = (el) => ((el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '') + '')
    .replace(/\s+/g, ' ').trim();
  const css = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    const tag = el.tagName.toLowerCase();
    if (name) return tag + '[name="' + name.replace(/"/g, '\\"') + '"]';
    return '';
  };
  const nodes = Array.from(document.querySelectorAll(
    'button, a, input, select, textarea, [role=button]'
  )).filter(visible);
  const elements = nodes.slice(0, 24).map((el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    text: textOf(el).slice(0, 60),
    selector: css(el),
  }));
  const body = document.body ? document.body.innerText : '';
  return {
    url: location.href,
    title: document.title,
    text: body.replace(/\n{3,}/g, '\n\n').slice(0, 1800),
    fullText: body,
    elements: elements,
  };
}
"""


class Stopped(Exception):
    """Пользователь нажал «Стоп»."""


class AgentStop(Exception):
    """ИИ-агент решил остановить весь запуск."""

    def __init__(self, say: str = "") -> None:
        super().__init__(say)
        self.say = say


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
        agent = bool(config.get("agent_enabled", False))
        agent_max = max(1, min(20, int(config.get("agent_max") or 4)))
        agent_wait = max(0.2, int(config.get("agent_wait_ms") or retry_wait * 1000) / 1000)
        agent_goal = (config.get("agent_goal") or "").strip()[:300]
        steps = config.get("steps") or []
        pre_steps = config.get("pre_steps") or []

        if not steps:
            self.log("Не настроено ни одного шага ввода", "error")
            self._set_state("error")
            return

        self._set_state("running")
        self.log(f"Старт. Строк к обработке: {self.total}")
        self.log(f"Браузер: {'без окна (headless)' if headless else 'с окном'}")
        if retry_until and not agent:
            self.log(
                f"Повтор до изменения: до {retry_max} попыток, ожидание {int(retry_wait * 1000)} мс, "
                f"сравнение: {retry_selector or 'вся страница'}"
            )

        agent_info: dict[str, Any] = {}
        if agent:
            try:
                import ai

                agent_info = ai.status()
            except Exception:  # noqa: BLE001
                agent_info = {"mode": "offline", "label": "встроенная логика"}
            self.log(
                f"ИИ-агент: до {agent_max} попыток, ожидание {int(agent_wait * 1000)} мс, "
                f"решения: {agent_info.get('label') or 'встроенная логика'}",
                "ok" if agent_info.get("mode") in {"ollama", "openai"} else "info",
            )
            if agent_goal:
                self.log(f"  цель: {agent_goal}")
            if agent_info.get("mode") not in {"ollama", "openai"}:
                self.log("  локальная модель не найдена — работают встроенные правила решений", "warn")
            if retry_until:
                self.log("  повтор до изменения не используется: попытки ведёт агент")

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

                    if agent:
                        outcome = self._agent_row(
                            page,
                            steps,
                            headers,
                            row,
                            timeout,
                            context,
                            num,
                            url,
                            agent_max,
                            agent_wait,
                            agent_goal,
                            agent_info,
                            reload_each,
                            shoot,
                            stop_on_error,
                        )
                        if outcome == "next":
                            processed += 1
                            self.done = processed
                        else:
                            failed += 1
                        time.sleep(max(0, wait_after))
                        continue

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
        except AgentStop as exc:
            self.log(f"ИИ-агент остановил запуск: {exc.say or 'результат достигнут'}", "ok")
            self._set_state("done")
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

    # ---------- ИИ-агент ----------

    def _agent_row(
        self,
        page: Any,
        steps: list[dict[str, Any]],
        headers: list[str],
        row: list[str],
        timeout: int,
        context: Any,
        num: int,
        url: str,
        agent_max: int,
        agent_wait: float,
        agent_goal: str,
        agent_info: dict[str, Any],
        reload_each: bool,
        shoot: bool,
        stop_on_error: bool,
    ) -> str:
        """Ведёт одну строку: ИИ после каждой попытки решает, что делать."""
        import ai

        attempt = 0
        last = ""
        submit_selector = ""
        while attempt < agent_max:
            if self._stop.is_set():
                raise Stopped()
            attempt += 1
            submit_only = last == "submit"

            if attempt > 1 and (last == "reload" or reload_each):
                self.log("  перезагружаю страницу перед повтором")
                page.goto(url, wait_until="domcontentloaded")

            before = self._agent_snapshot(page).get("fingerprint") or ""

            try:
                if submit_only:
                    self.log("  ИИ: повторная отправка без ввода")
                    clicked = (
                        self._agent_click(page, submit_selector, "отправка")
                        if submit_selector
                        else False
                    )
                    if not clicked:
                        auto = self._agent_find(
                            page,
                            ["отправ", "сохран", "submit", "send", "готово", "далее"],
                            "submit",
                        )
                        clicked = self._agent_click(page, auto, "отправка") if auto else False
                    if not clicked:
                        self.log("  ИИ: кнопка отправки не найдена — выполняю шаги заново", "warn")
                        self._exec_steps(page, steps, headers, row, timeout, context)
                else:
                    self._exec_steps(page, steps, headers, row, timeout, context)
            except Stopped:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log(f"  ошибка: {self._short(exc)}", "error")
                if shoot:
                    self._screenshot(page, num)
                if stop_on_error:
                    raise
                return "fail"

            self._sleep(agent_wait)
            snapshot = self._agent_snapshot(page)
            after = snapshot.get("fingerprint") or ""
            changed = bool(before and after and before != after)
            meta = {
                "goal": agent_goal,
                "row": self._brief(headers, row),
                "attempt": attempt,
                "max_attempts": agent_max,
                "changed": changed,
                "url": snapshot.get("url") or url,
            }
            decision = self._agent_decide(meta, snapshot, agent_info)
            kind = decision["decision"]
            say = decision.get("say") or ""
            self.log(
                f"  ИИ ({attempt}/{agent_max}): {ai.AGENT_LABELS.get(kind, kind)}"
                + (f" — {say}" if say else ""),
                "ok" if kind == "next" else "info",
            )
            selector = (decision.get("selector") or "").strip()

            if kind == "next":
                return "next"
            if kind == "stop":
                raise AgentStop(say or "результат достигнут")
            if kind == "fail":
                self.log("  строка не поддалась — перехожу к следующей", "warn")
                if shoot:
                    self._screenshot(page, num)
                return "fail"

            submit_selector = selector
            if kind == "clear":
                clicked = self._agent_click(page, selector, "очистка") if selector else False
                if not clicked:
                    auto = self._agent_find(page, ["очист", "сброс", "reset", "clear"], "clear")
                    clicked = self._agent_click(page, auto, "очистка") if auto else False
                if not clicked:
                    self.log("  ИИ: кнопка очистки не найдена — повторяю шаги", "warn")
                self._sleep(0.3)
                last = "clear"
            elif kind == "submit":
                last = "submit"
            elif kind == "reload":
                last = "reload"
            else:
                last = "repeat"

        self.log(f"  за {agent_max} попыток результат не достигнут", "warn")
        if shoot:
            self._screenshot(page, num)
        return "fail"

    def _agent_decide(
        self,
        meta: dict[str, Any],
        snapshot: dict[str, Any],
        info: dict[str, Any],
    ) -> dict[str, Any]:
        import ai

        try:
            decision = ai.agent_decide(meta, snapshot, info=info or None)
        except Exception as exc:  # noqa: BLE001
            self.log(f"    ИИ не ответил ({self._short(exc)}) — решаю встроенными правилами", "warn")
            decision = ai.agent_decide(meta, snapshot, info={"mode": "offline"})
        if not isinstance(decision, dict):
            decision = {}
        if decision.get("decision") not in ai.AGENT_DECISIONS:
            decision = {"decision": "repeat", "say": "повторяю", "selector": ""}
        return decision

    def _agent_snapshot(self, page: Any) -> dict[str, Any]:
        """Снимок страницы: URL, заголовок, текст, элементы и отпечаток для сравнения."""
        try:
            data = page.evaluate(AGENT_SNAPSHOT_JS) or {}
        except Exception:  # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            data = {}
        url = str(data.get("url") or getattr(page, "url", "") or "")
        full_text = str(data.get("fullText") or "")
        fingerprint = hashlib.sha1(
            f"{url}\n{full_text}".encode("utf-8", "ignore")
        ).hexdigest()
        elements = data.get("elements")
        return {
            "url": url,
            "title": str(data.get("title") or "")[:200],
            "text": str(data.get("text") or "")[:1800],
            "elements": elements if isinstance(elements, list) else [],
            "fingerprint": fingerprint,
        }

    def _agent_click(self, page: Any, selector: str, what: str) -> bool:
        selector = (selector or "").strip()
        if not selector:
            return False
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                return False
            locator.click(timeout=8000)
            self.log(f"    ИИ: {what} → {selector}")
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"    ИИ: не удалось нажать «{what}» ({self._short(exc)})", "warn")
            return False

    @staticmethod
    def _agent_find(page: Any, words: list[str], key: str) -> str:
        """Ищет кнопку по тексту и помечает её, возвращая безопасный селектор."""
        try:
            selector = page.evaluate(
                """([key, words]) => {
                    const nodes = Array.from(document.querySelectorAll(
                      'button, a, input[type=submit], input[type=button], [role=button]'
                    ));
                    for (const el of nodes) {
                      const text = ((el.innerText || el.value || '') + ' ' +
                        (el.getAttribute('aria-label') || '')).toLowerCase();
                      if (words.some((w) => text.includes(w))) {
                        el.setAttribute('data-vv-agent', key);
                        return '[data-vv-agent="' + key + '"]';
                      }
                    }
                    return '';
                }""",
                [key, words],
            )
            return str(selector or "")
        except Exception:  # noqa: BLE001
            return ""

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
