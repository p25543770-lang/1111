"""Локальный ИИ-ассистент «АвтоВвода».

Никаких облаков: ассистент общается только с моделью на этом же компьютере
(Ollama по умолчанию, либо любой OpenAI-совместимый сервер вроде LM Studio
или llama.cpp). Если модели нет — включается встроенный разбор команд,
который тоже работает полностью локально.

Ассистент не просто отвечает: он возвращает список действий, которые
интерфейс применяет к приложению (адрес сайта, данные, шаги, запуск).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

OLLAMA_URL = os.environ.get("AUTOVVOD_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OPENAI_URL = os.environ.get("AUTOVVOD_AI_URL", "").rstrip("/")
MODEL = os.environ.get("AUTOVVOD_AI_MODEL", "")

ACTION_TYPES = {
    "set_url",
    "open_site",
    "load_demo",
    "parse_data",
    "add_steps",
    "clear_steps",
    "start_run",
    "stop_run",
    "set_retry",
    "set_agent",
    "open_panel",
}

HELP = (
    "Я работаю полностью на этом компьютере. Умею: загрузить демо-данные, "
    "открыть сайт внутри приложения, разобрать вставленную таблицу, добавить шаги, "
    "включить повтор до изменения страницы, включить ИИ-агента (он сам решает, что делать после "
    "каждой попытки), запустить или остановить ввод и показать состояние. "
    "Например: «загрузи демо и запусти», «открой https://example.com/form», "
    "«включи агента, пусть сам решает», «повторяй, пока страница не изменится», «сколько строк и шагов?»."
)


# ------------------------------------------------------------------ сеть


def _request(url: str, payload: dict[str, Any] | None = None, timeout: float = 2.0) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — только localhost
        return json.loads(response.read().decode("utf-8", "replace"))


def _ollama_models() -> list[str]:
    try:
        data = _request(f"{OLLAMA_URL}/api/tags", timeout=1.2)
    except Exception:  # noqa: BLE001
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names = []
    for item in models:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _openai_models() -> list[str]:
    if not OPENAI_URL:
        return []
    base = OPENAI_URL if OPENAI_URL.endswith("/v1") else f"{OPENAI_URL}/v1"
    try:
        data = _request(f"{base}/models", timeout=1.2)
    except Exception:  # noqa: BLE001
        return []
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [str(it.get("id")) for it in items if isinstance(it, dict) and it.get("id")]


def status() -> dict[str, Any]:
    """Проверяет, есть ли локальная модель. Всегда local=True."""
    models = _ollama_models()
    if models:
        model = MODEL if MODEL in models else models[0]
        return {
            "local": True,
            "mode": "ollama",
            "endpoint": OLLAMA_URL,
            "model": model,
            "models": models,
            "label": f"Ollama · {model}",
        }

    openai_models = _openai_models()
    if openai_models:
        model = MODEL if MODEL in openai_models else openai_models[0]
        return {
            "local": True,
            "mode": "openai",
            "endpoint": OPENAI_URL,
            "model": model,
            "models": openai_models,
            "label": f"Локальный сервер · {model}",
        }

    return {
        "local": True,
        "mode": "offline",
        "endpoint": "",
        "model": "",
        "models": [],
        "label": "Встроенный локальный режим",
    }


def _ollama_chat(
    model: str, system: str, messages: list[dict[str, str]], timeout: float = 90.0
) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}] + messages,
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    data = _request(f"{OLLAMA_URL}/api/chat", payload, timeout=timeout)
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return ""


def _openai_chat(
    model: str, system: str, messages: list[dict[str, str]], timeout: float = 90.0
) -> str:
    base = OPENAI_URL if OPENAI_URL.endswith("/v1") else f"{OPENAI_URL}/v1"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    data = _request(f"{base}/chat/completions", payload, timeout=timeout)
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")
    return ""


# ------------------------------------------------------------------ протокол


def _system_prompt(context: dict[str, Any]) -> str:
    state = json.dumps(context or {}, ensure_ascii=False)
    return (
        "Ты — локальный ассистент приложения «АвтоВвод». Оно по очереди вводит строки "
        "таблицы в поля сайта. Отвечай по-русски, коротко и по делу.\n\n"
        f"Текущее состояние приложения (JSON): {state}\n\n"
        "Ты можешь управлять приложением. Верни ТОЛЬКО JSON без markdown:\n"
        '{"say": "текст ответа", "actions": [ ... ]}\n'
        "Допустимые действия:\n"
        '- {"type": "set_url", "url": "https://..."}\n'
        '- {"type": "open_site", "url": "https://..."} — открыть сайт внутри приложения\n'
        '- {"type": "load_demo"} — загрузить демо-данные и готовые шаги\n'
        '- {"type": "parse_data", "text": "Имя\\tТелефон\\nИван\\t+7999..."}\n'
        '- {"type": "add_steps", "steps": [{"selector": "#name", "action": "fill", '
        '"column": "Имя", "value_mode": "column", "value": ""}]}\n'
        '- {"type": "clear_steps"}\n'
        '- {"type": "start_run"}\n'
        '- {"type": "stop_run"}\n'
        '- {"type": "set_retry", "enabled": true, "max_attempts": 5, "wait_ms": 1500, '
        '"stop_on_change": true, "selector": ""} — повторять строку, пока страница не изменится\n'
        '- {"type": "open_panel", "panel": "site|data|steps|run|ai"}\n'
        "Если действие не нужно — верни пустой список actions. Не выдумывай селекторы, "
        "если пользователь их не назвал. Если просят сценарий «очистить → ввести заново → нажать "
        "и повторять, пока не изменится», включи set_retry со stop_on_change=true, а шаги добавь "
        "только если пользователь назвал селекторы."
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            return None
    return None


def _clean_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    actions = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        if kind not in ACTION_TYPES:
            continue
        action: dict[str, Any] = {"type": kind}
        if kind in {"set_url", "open_site"}:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            action["url"] = url
        elif kind == "parse_data":
            text = str(item.get("text") or "")
            if not text.strip():
                continue
            action["text"] = text
            action["has_header"] = bool(item.get("has_header", True))
        elif kind == "add_steps":
            steps = item.get("steps")
            if not isinstance(steps, list) or not steps:
                continue
            action["steps"] = steps[:20]
        elif kind == "set_retry":
            action["enabled"] = bool(item.get("enabled", True))
            try:
                attempts = int(item.get("max_attempts"))
                if attempts > 0:
                    action["max_attempts"] = min(50, attempts)
            except (TypeError, ValueError):
                pass
            try:
                wait_ms = int(item.get("wait_ms"))
                if wait_ms > 0:
                    action["wait_ms"] = min(60000, wait_ms)
            except (TypeError, ValueError):
                pass
            if "stop_on_change" in item:
                action["stop_on_change"] = bool(item["stop_on_change"])
            if isinstance(item.get("selector"), str):
                action["selector"] = item["selector"][:200]
        elif kind == "set_agent":
            action["enabled"] = bool(item.get("enabled", True))
            try:
                attempts = int(item.get("max_attempts"))
                if attempts > 0:
                    action["max_attempts"] = min(20, attempts)
            except (TypeError, ValueError):
                pass
            try:
                wait_ms = int(item.get("wait_ms"))
                if wait_ms > 0:
                    action["wait_ms"] = min(60000, wait_ms)
            except (TypeError, ValueError):
                pass
            if isinstance(item.get("goal"), str):
                action["goal"] = item["goal"][:300]
        elif kind == "open_panel":
            panel = str(item.get("panel") or "")
            if panel not in {"site", "data", "steps", "run", "ai"}:
                continue
            action["panel"] = panel
        actions.append(action)
    return actions


# ------------------------------------------------------------------ офлайн-режим


def _status_text(context: dict[str, Any]) -> str:
    rows = context.get("rows", 0)
    steps = context.get("steps") or []
    parts = [f"строк: {rows}", f"шагов: {len(steps) if isinstance(steps, list) else steps}"]
    if context.get("url"):
        parts.append("сайт: открыт в приложении" if context.get("site_open") else "адрес указан, сайт не открыт")
    else:
        parts.append("адрес сайта не указан")
    parts.append("идёт запуск" if context.get("running") else "запуск не идёт")
    return "Сейчас: " + ", ".join(str(p) for p in parts) + "."


def _extract_table(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "\t" in line or ";" in line or ("," in line and not re.search(r"https?://", line)):
            chunk = "\n".join(lines[index:]).strip()
            if len(chunk) > 2:
                return chunk
    return ""


def _offline(text: str, context: dict[str, Any]) -> dict[str, Any]:
    t = (text or "").strip()
    low = t.lower()
    actions: list[dict[str, Any]] = []
    say = ""

    url_match = re.search(r"https?://\S+", t)
    is_stop = any(word in low for word in ("стоп", "останов", "stop"))
    retry_hint = any(
        word in low
        for word in ("повтор", "пока не изменит", "до изменени", "если изменил", "изменится")
    )
    agent_hint = any(
        word in low
        for word in ("агент", "сам реша", "самостоятельн", "автономн", "пусть решает", "сам дума")
    )
    if retry_hint and not agent_hint:
        # «остановись, когда изменится» — это не команда «стоп»
        is_stop = False
        retry_action: dict[str, Any] = {"type": "set_retry", "enabled": True, "stop_on_change": True}
        attempt = re.search(r"(\d+)\s*(?:раз|попыт)", low)
        if attempt:
            retry_action["max_attempts"] = max(1, min(50, int(attempt.group(1))))
        actions.append(retry_action)
    if agent_hint:
        is_stop = False
        agent_action: dict[str, Any] = {"type": "set_agent", "enabled": True}
        attempt = re.search(r"(\d+)\s*(?:раз|попыт)", low)
        if attempt:
            agent_action["max_attempts"] = max(1, min(20, int(attempt.group(1))))
        goal_match = re.search(r"цел[ьи]\s*[:\-—]?\s*(.+)$", t, re.I)
        if goal_match:
            goal_text = goal_match.group(1).strip(" .,;")
            if goal_text:
                agent_action["goal"] = goal_text[:300]
        actions.append(agent_action)
    is_start = any(word in low for word in ("запуст", "старт", "start", "run")) and not is_stop

    if url_match:
        url = url_match.group(0)
        actions.append({"type": "open_site", "url": url})
        say = f"Открываю {url} внутри приложения."
        if is_start:
            actions.append({"type": "start_run"})
            say += " Затем запускаю сценарий."
    elif "демо" in low and any(word in low for word in ("загруз", "включ", "покаж", "пример")):
        actions.append({"type": "load_demo"})
        if is_start:
            actions.append({"type": "start_run"})
            say = "Загружаю демо-данные и сразу запускаю сценарий."
        else:
            say = "Загружаю демо-данные и готовые шаги."
    elif is_stop:
        actions.append({"type": "stop_run"})
        say = "Останавливаю выполнение."
    elif is_start:
        actions.append({"type": "start_run"})
        say = "Запускаю сценарий. Следите за журналом в разделе «Запуск»."
    elif "очист" in low and "шаг" in low:
        actions.append({"type": "clear_steps"})
        say = "Очищаю список шагов."
    elif any(word in low for word in ("статус", "сколько", "состояни", "что сейч", "status")):
        say = _status_text(context)
    elif any(word in low for word in ("встав", "данные", "таблиц")):
        table = _extract_table(t)
        if table:
            actions.append({"type": "parse_data", "text": table, "has_header": True})
            say = "Разбираю таблицу из сообщения и добавляю строки."
        else:
            say = "Пришлите строки таблицы следующим сообщением — колонки через табуляцию, запятую или «;»."
    elif agent_hint:
        say = (
            "Включаю ИИ-агента: после каждой попытки он смотрит на страницу и сам решает — "
            "повторить, очистить, отправить ещё раз, перезагрузить или остановиться."
        )
    elif retry_hint:
        say = (
            "Включаю повтор: буду выполнять строку заново, пока содержимое страницы не изменится, "
            "и остановлюсь, как только изменение появится."
        )
    elif any(word in low for word in ("помощ", "help", "что умеешь", "команд")):
        say = HELP
    else:
        say = HELP

    if agent_hint and "агент" not in say.lower():
        say = (say + " ИИ-агент включён: решения по попыткам принимает он.").strip()
    elif retry_hint and "повтор" not in say.lower() and "повтор включён" not in say.lower():
        say = (say + " Повтор включён: остановка при изменении страницы.").strip()

    return {"say": say, "actions": actions}


# ------------------------------------------------------------------ ИИ-агент

AGENT_DECISIONS = ("next", "repeat", "clear", "submit", "reload", "stop", "fail")

AGENT_LABELS = {
    "next": "успех — следующая строка",
    "repeat": "повторить шаги",
    "clear": "очистить и повторить",
    "submit": "нажать отправку ещё раз",
    "reload": "перезагрузить страницу",
    "stop": "остановить запуск",
    "fail": "строка не поддалась",
}

_ERROR_HINTS = (
    "ошиб", "неверн", "некоррект", "заполните", "обязательн", "недопустим",
    "не может", "не удалось", "повторите", "error", "invalid", "required", "failed",
)
_SUCCESS_HINTS = (
    "успеш", "сохранен", "сохранён", "принят", "отправлен", "добавлен",
    "записан", "спасибо", "success", "saved", "created",
)


def _agent_offline(meta: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Встроенные решения без модели: по факту изменения и тексту страницы."""
    attempt = int(meta.get("attempt") or 1)
    limit = max(1, int(meta.get("max_attempts") or 4))
    changed = bool(meta.get("changed"))
    text = str(snapshot.get("text") or "").lower()
    error = any(word in text for word in _ERROR_HINTS)

    if changed and not error:
        success = any(word in text for word in _SUCCESS_HINTS)
        say = (
            "страница сообщает об успехе — строка принята"
            if success
            else "страница изменилась — считаю попытку удачной"
        )
        return {"decision": "next", "say": say, "selector": "", "mode": "offline"}

    if changed and error:
        if attempt >= limit:
            return {
                "decision": "fail",
                "say": "на странице ошибка, попытки кончились",
                "selector": "",
                "mode": "offline",
            }
        return {
            "decision": "clear",
            "say": "на странице ошибка — очищаю и пробую заново",
            "selector": "",
            "mode": "offline",
        }

    if attempt >= limit:
        return {
            "decision": "fail",
            "say": f"за {limit} попыток страница не изменилась",
            "selector": "",
            "mode": "offline",
        }
    if attempt == 1:
        return {"decision": "clear", "say": "изменений нет — очищаю и повторяю ввод", "selector": "", "mode": "offline"}
    if attempt == 2:
        return {"decision": "repeat", "say": "изменений нет — повторяю шаги", "selector": "", "mode": "offline"}
    if attempt == 3:
        return {"decision": "reload", "say": "изменений нет — перезагружаю страницу", "selector": "", "mode": "offline"}
    return {"decision": "submit", "say": "изменений нет — нажимаю отправку ещё раз", "selector": "", "mode": "offline"}


def _agent_prompt(meta: dict[str, Any], snapshot: dict[str, Any]) -> str:
    goal = str(meta.get("goal") or "добиться, чтобы данные принялись").strip()
    snap = {key: snapshot.get(key, "") for key in ("url", "title", "text", "elements")}
    changed = "да" if meta.get("changed") else "нет"
    return (
        "Ты — локальный ИИ-агент приложения «АвтоВвод». Оно вводит строки таблицы в поля сайта. "
        "После каждой попытки ты смотришь на состояние страницы и решаешь, что делать дальше.\n\n"
        f"Цель пользователя: {goal}\n"
        f"Текущая строка: {meta.get('row')}\n"
        f"Попытка {meta.get('attempt')} из {meta.get('max_attempts')}. "
        f"Страница изменилась после действий: {changed}.\n\n"
        "Состояние страницы (JSON):\n"
        + json.dumps(snap, ensure_ascii=False)[:3500]
        + "\n\nВерни ТОЛЬКО JSON без markdown:\n"
        '{"decision": "next", "selector": "", "say": "коротко по-русски"}\n'
        "decision — одно из:\n"
        "next — результат достигнут, переходить к следующей строке;\n"
        "repeat — повторить шаги ввода;\n"
        "clear — нажать кнопку очистки и повторить ввод;\n"
        "submit — нажать кнопку отправки ещё раз, без повторного ввода;\n"
        "reload — перезагрузить страницу и повторить ввод;\n"
        "stop — остановить весь запуск;\n"
        "fail — строка не поддалась, перейти к следующей.\n"
        "selector — необязательный CSS-селектор кнопки для clear/submit, только из списка elements. "
        "Если страница показывает ошибку, не считай это успехом. Не придумывай селекторы."
    )


def agent_decide(
    meta: dict[str, Any],
    snapshot: dict[str, Any],
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Решение агента по текущей попытке: next/repeat/clear/submit/reload/stop/fail."""
    meta = meta or {}
    snapshot = snapshot or {}
    info = info or status()
    mode = info.get("mode") or "offline"
    if mode in {"ollama", "openai"} and info.get("model"):
        system = _agent_prompt(meta, snapshot)
        messages = [{"role": "user", "content": "Прими решение по текущей попытке. Ответ — только JSON."}]
        try:
            if mode == "ollama":
                raw = _ollama_chat(info["model"], system, messages, timeout=30.0)
            else:
                raw = _openai_chat(info["model"], system, messages, timeout=30.0)
            parsed = _extract_json(raw) or {}
            decision = str(parsed.get("decision") or "").strip().lower()
            if decision in AGENT_DECISIONS:
                selector = parsed.get("selector")
                return {
                    "decision": decision,
                    "say": str(parsed.get("say") or "").strip()[:200],
                    "selector": selector[:200] if isinstance(selector, str) else "",
                    "mode": "model",
                }
        except Exception:  # noqa: BLE001 — агент не должен ломать запуск
            pass
    return _agent_offline(meta, snapshot)


# ------------------------------------------------------------------ точка входа


def chat(messages: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ответ ассистента + действия для интерфейса."""
    context = context or {}
    history: list[dict[str, str]] = []
    for item in (messages or [])[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content[:4000]})

    last_user = ""
    for item in reversed(history):
        if item["role"] == "user":
            last_user = item["content"]
            break

    info = status()
    mode = info["mode"]

    if mode in {"ollama", "openai"}:
        system = _system_prompt(context)
        try:
            if mode == "ollama":
                raw = _ollama_chat(info["model"], system, history)
            else:
                raw = _openai_chat(info["model"], system, history)
            parsed = _extract_json(raw)
            if parsed:
                say = str(parsed.get("say") or parsed.get("reply") or "").strip()
                actions = _clean_actions(parsed.get("actions"))
                if say or actions:
                    return {
                        **info,
                        "say": say or "Готово.",
                        "actions": actions,
                        "answer_mode": "model",
                    }
            if raw.strip():
                return {**info, "say": raw.strip(), "actions": [], "answer_mode": "model"}
        except Exception as exc:  # noqa: BLE001
            fallback = _offline(last_user, context)
            fallback["say"] = (
                f"{fallback['say']}\n\nМодель не ответила ({exc.__class__.__name__}), "
                "сработал встроенный локальный режим."
            )
            return {**info, **fallback, "answer_mode": "offline"}

    return {**info, **_offline(last_user, context), "answer_mode": "offline"}
