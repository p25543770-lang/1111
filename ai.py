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
    "open_panel",
}

HELP = (
    "Я работаю полностью на этом компьютере. Умею: загрузить демо-данные, "
    "открыть сайт внутри приложения, разобрать вставленную таблицу, добавить шаги, "
    "запустить или остановить ввод и показать состояние. "
    "Например: «загрузи демо и запусти», «открой https://example.com/form», "
    "«сколько строк и шагов?»."
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


def _ollama_chat(model: str, system: str, messages: list[dict[str, str]]) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}] + messages,
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    data = _request(f"{OLLAMA_URL}/api/chat", payload, timeout=90.0)
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return ""


def _openai_chat(model: str, system: str, messages: list[dict[str, str]]) -> str:
    base = OPENAI_URL if OPENAI_URL.endswith("/v1") else f"{OPENAI_URL}/v1"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    data = _request(f"{base}/chat/completions", payload, timeout=90.0)
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
        '- {"type": "open_panel", "panel": "site|data|steps|run|ai"}\n'
        "Если действие не нужно — верни пустой список actions. Не выдумывай селекторы, "
        "если пользователь их не назвал."
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
    elif any(word in low for word in ("помощ", "help", "что умеешь", "команд")):
        say = HELP
    else:
        say = HELP

    return {"say": say, "actions": actions}


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
