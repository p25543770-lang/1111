"""Локальный сервер приложения: интерфейс + API для автоматизации ввода."""

from __future__ import annotations

import csv
import io
import json
import os
import queue
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from picker import picker
from runner import runner

FROZEN = bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """Папка с ресурсами: рядом с исходниками или внутри собранного exe."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    """Папка для пользовательских данных (настроек, снимков)."""
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "AutoVvod"
    return Path(__file__).resolve().parent


BASE_DIR = _bundle_dir()
STATIC_DIR = BASE_DIR / "static"
CONFIGS_DIR = _data_dir() / "configs"

app = FastAPI(title="АвтоВвод")


# ---------------------------------------------------------------- данные

class ParseText(BaseModel):
    text: str
    has_header: bool = True
    delimiter: str | None = None


class RunRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class SaveConfig(BaseModel):
    name: str
    config: dict[str, Any]


def detect_delimiter(sample: str) -> str:
    counts = {d: sample.count(d) for d in ["\t", ";", ",", "|"]}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else "\t"


def table_from_rows(rows: list[list[str]], has_header: bool) -> dict[str, Any]:
    rows = [[("" if c is None else str(c)).strip() for c in r] for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return {"headers": [], "rows": []}
    width = max(len(r) for r in rows)

    def pad(r: list[str]) -> list[str]:
        return r + [""] * (width - len(r))

    rows = [pad(r) for r in rows]
    if has_header:
        headers = rows[0]
        headers = [h or f"Колонка {i + 1}" for i, h in enumerate(headers)]
        body = rows[1:]
    else:
        headers = [f"Колонка {i + 1}" for i in range(width)]
        body = rows
    # убираем повторы имён колонок
    seen: dict[str, int] = {}
    final = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            final.append(f"{h} ({seen[h]})")
        else:
            seen[h] = 0
            final.append(h)
    return {"headers": final, "rows": body}


def parse_text(text: str, has_header: bool = True, delimiter: str | None = None) -> dict[str, Any]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        return {"headers": [], "rows": []}
    delim = delimiter or detect_delimiter(text.split("\n", 1)[0])
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return table_from_rows(rows, has_header)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    if not letters:
        return 0
    n = 0
    for ch in letters.group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def parse_xlsx_fallback(data: bytes) -> dict[str, Any]:
    """Минимальный разбор .xlsx без сторонних библиотек: берём первый лист."""
    q = lambda tag: f"{{{XLSX_NS}}}{tag}"  # noqa: E731
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.iter(q("si")):
                shared.append("".join(t.text or "" for t in si.iter(q("t"))))
        sheets = [n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        if not sheets:
            raise ValueError("в файле нет листов")
        sheets.sort(key=lambda n: int(re.findall(r"\d+", n)[-1]))
        root = ET.fromstring(zf.read(sheets[0]))
        rows: list[list[str]] = []
        for row_el in root.iter(q("row")):
            values: dict[int, str] = {}
            for c in row_el.iter(q("c")):
                idx = _col_index(c.get("r") or "")
                t = c.get("t")
                v = c.find(q("v"))
                text = ""
                if t == "s" and v is not None and v.text:
                    i = int(v.text)
                    text = shared[i] if i < len(shared) else ""
                elif t == "inlineStr":
                    is_el = c.find(q("is"))
                    if is_el is not None:
                        text = "".join(x.text or "" for x in is_el.iter(q("t")))
                elif v is not None:
                    text = v.text or ""
                values[idx] = text
            if values:
                width = max(values) + 1
                rows.append([values.get(i, "") for i in range(width)])
    return table_from_rows(rows, True)


@app.post("/api/data/text")
def data_from_text(payload: ParseText) -> dict[str, Any]:
    return parse_text(payload.text, payload.has_header, payload.delimiter)


@app.post("/api/data/file")
async def data_from_file(file: UploadFile = File(...), has_header: bool = True) -> dict[str, Any]:
    data = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xlsm")):
            try:
                from openpyxl import load_workbook

                wb = load_workbook(io.BytesIO(data), data_only=True)
                ws = wb.worksheets[0]
                rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
                return table_from_rows(rows, has_header)
            except ImportError:
                return parse_xlsx_fallback(data)
        text = None
        for enc in ("utf-8-sig", "cp1251", "utf-16"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise HTTPException(400, "не удалось определить кодировку файла")
        return parse_text(text, has_header)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"не удалось прочитать файл: {exc}") from exc


# ---------------------------------------------------------------- запуск

@app.post("/api/run")
def start_run(payload: RunRequest) -> dict[str, Any]:
    if runner.running:
        raise HTTPException(409, "автоматизация уже запущена")
    if not payload.rows:
        raise HTTPException(400, "список данных пуст")
    runner.start(payload.config, payload.headers, payload.rows)
    return {"ok": True}


@app.post("/api/stop")
def stop_run() -> dict[str, Any]:
    runner.stop()
    return {"ok": True}


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "state": runner.state,
        "running": runner.running,
        "done": runner.done,
        "total": runner.total,
    }


@app.get("/api/history")
def history() -> dict[str, Any]:
    return {"events": runner.history()}


@app.get("/api/stream")
def stream() -> StreamingResponse:
    sub = runner.subscribe()

    def gen():
        try:
            for ev in runner.history()[-200:]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = sub.get(timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            runner.unsubscribe(sub)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- выбор полей

class OpenUrl(BaseModel):
    url: str
    headless: bool = False


class Selector(BaseModel):
    selector: str


class ClickAt(BaseModel):
    x: float
    y: float


class ScrollBy(BaseModel):
    dy: int = 0


@app.get("/api/picker/state")
def picker_state() -> dict[str, Any]:
    return picker.state()


@app.post("/api/picker/open")
def picker_open(payload: OpenUrl) -> dict[str, Any]:
    url = payload.url.strip()
    if not url:
        raise HTTPException(400, "не указан адрес")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    res = picker.launch(url, payload.headless)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "не удалось открыть сайт")
    return res


@app.post("/api/picker/pick/start")
def picker_pick_start() -> dict[str, Any]:
    res = picker.start_pick()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "браузер не открыт")
    return res


@app.post("/api/picker/pick/stop")
def picker_pick_stop() -> dict[str, Any]:
    res = picker.stop_pick()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "браузер не открыт")
    return res


@app.get("/api/picker/picked")
def picker_picked() -> dict[str, Any]:
    return {"picked": picker.state()["picked"]}


@app.post("/api/picker/probe")
def picker_probe(payload: Selector) -> dict[str, Any]:
    res = picker.probe(payload.selector)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "не удалось проверить селектор")
    return res


@app.get("/api/picker/screenshot")
def picker_screenshot() -> dict[str, Any]:
    res = picker.screenshot()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "снимок не получился")
    return res


@app.post("/api/picker/click_at")
def picker_click_at(payload: ClickAt) -> dict[str, Any]:
    res = picker.click_at(payload.x, payload.y)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "клик не удался")
    return res


@app.post("/api/picker/scroll")
def picker_scroll(payload: ScrollBy) -> dict[str, Any]:
    res = picker.scroll(payload.dy)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "прокрутка не удалась")
    return res


@app.post("/api/picker/goto")
def picker_goto(payload: OpenUrl) -> dict[str, Any]:
    res = picker.goto(payload.url)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "переход не удался")
    return res


@app.post("/api/picker/close")
def picker_close() -> dict[str, Any]:
    picker.close()
    return {"ok": True}


# ---------------------------------------------------------------- сохранение настроек

@app.get("/api/configs")
def list_configs() -> dict[str, Any]:
    CONFIGS_DIR.mkdir(exist_ok=True)
    items = []
    for p in sorted(CONFIGS_DIR.glob("*.json")):
        items.append({"name": p.stem, "mtime": p.stat().st_mtime})
    return {"items": items}


@app.get("/api/configs/{name}")
def get_config(name: str) -> JSONResponse:
    path = CONFIGS_DIR / f"{_safe(name)}.json"
    if not path.exists():
        raise HTTPException(404, "настройки не найдены")
    return JSONResponse(json.loads(path.read_text("utf-8")))


@app.post("/api/configs")
def save_config(payload: SaveConfig) -> dict[str, Any]:
    CONFIGS_DIR.mkdir(exist_ok=True)
    path = CONFIGS_DIR / f"{_safe(payload.name)}.json"
    path.write_text(json.dumps(payload.config, ensure_ascii=False, indent=2), "utf-8")
    return {"ok": True, "name": path.stem}


@app.delete("/api/configs/{name}")
def delete_config(name: str) -> dict[str, Any]:
    path = CONFIGS_DIR / f"{_safe(name)}.json"
    if path.exists():
        path.unlink()
    return {"ok": True}


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip(" .")
    return cleaned[:80] or "config"


# ---------------------------------------------------------------- демо-страница

@app.get("/demo/form.html")
def demo_form() -> FileResponse:
    return FileResponse(BASE_DIR / "demo" / "form.html")


# ---------------------------------------------------------------- статика

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
