"""Визуальный выбор полей на сайте.

Открывается браузер с сайтом. Кликом по элементам пользователь отмечает поля
ввода, кнопку «Отправить» и т. п. — приложение получает надёжные CSS-селекторы.
Плюс есть режим проверки селектора и снимок страницы (для превью в интерфейсе).
"""

from __future__ import annotations

import base64
import queue
import threading
import time
from typing import Any

from runner import launch_options

OVERLAY = r"""
(() => {
  if (window.__afPickInstalled) { window.__afPickOn = true; return; }
  window.__afPickInstalled = true;
  window.__afPickOn = false;

  const cssPath = (el) => {
    if (!(el instanceof Element)) return '';
    if (el.id && /^[A-Za-z][\w\-]*$/.test(el.id)) return '#' + el.id;

    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      let part = node.nodeName.toLowerCase();
      if (node.id && /^[A-Za-z][\w\-]*$/.test(node.id)) {
        parts.unshift('#' + node.id);
        break;
      }
      const name = node.getAttribute('name');
      const itype = node.getAttribute('type');
      if (name && ['input', 'select', 'textarea', 'button'].includes(part)) {
        part += '[name="' + name + '"]';
        if (itype && part === 'input') part += '[type="' + itype + '"]';
        parts.unshift(part);
        break;
      }
      if (node.parentElement) {
        const same = Array.from(node.parentElement.children)
          .filter((c) => c.nodeName === node.nodeName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const friendly = (el) => {
    let t = '';
    if (el.labels && el.labels[0]) t = el.labels[0].innerText || '';
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) t = el.placeholder || '';
    if (!t) t = el.getAttribute('title') || '';
    if (!t) t = el.name || el.id || '';
    if (['BUTTON', 'A'].includes(el.nodeName)) {
      const txt = (el.innerText || '').trim();
      if (txt) t = txt;
    }
    return t.trim().replace(/\s+/g, ' ').slice(0, 50);
  };

  const labelOf = (el) => {
    const tag = el.nodeName.toLowerCase();
    const desc = '<' + tag + '>' + (el.id ? '#' + el.id : '');
    const name = friendly(el);
    return name ? name + '  ' + desc : desc;
  };

  const style = document.createElement('style');
  style.textContent = `
    .__af-hl { outline: 3px solid #22d3ee !important; outline-offset: 2px !important;
               background: rgba(34,211,238,.12) !important; cursor: crosshair !important; }
    .__af-tip { position: fixed; z-index: 2147483647; pointer-events: none;
                background: #0f172a; color: #e2e8f0; font: 12px/1.4 system-ui, sans-serif;
                padding: 4px 8px; border-radius: 6px; max-width: 60vw;
                box-shadow: 0 4px 16px rgba(0,0,0,.35); }
  `;
  document.documentElement.appendChild(style);

  let tip = document.createElement('div');
  tip.className = '__af-tip';
  tip.style.display = 'none';
  document.documentElement.appendChild(tip);
  let cur = null;

  const hl = (el) => {
    if (cur === el) return;
    if (cur) cur.classList.remove('__af-hl');
    cur = el;
    if (el && el !== document.documentElement && el !== document.body) {
      el.classList.add('__af-hl');
      const r = el.getBoundingClientRect();
      tip.textContent = labelOf(el);
      tip.style.display = 'block';
      tip.style.left = Math.min(r.left, window.innerWidth - 220) + 'px';
      tip.style.top = Math.max(4, r.top - 28) + 'px';
    } else {
      tip.style.display = 'none';
    }
  };

  const onOver = (e) => { if (window.__afPickOn) hl(e.target); };
  const onClick = (e) => {
    if (!window.__afPickOn) return;
    const el = e.target;
    if (!el || el === document.documentElement || el === document.body) return;
    e.preventDefault();
    e.stopPropagation();
    const sel = cssPath(el);
    const info = { selector: sel, title: friendly(el), label: labelOf(el),
                   tag: el.nodeName.toLowerCase(), type: el.type || '',
                   name: el.name || '', id: el.id || '' };
    if (window.__afEmit) window.__afEmit(info);
    el.style.outline = '3px solid #22c55e';
    setTimeout(() => { el.style.outline = ''; }, 600);
    return false;
  };
  document.addEventListener('mouseover', onOver, true);
  document.addEventListener('click', onClick, true);
})();
"""


class Picker:
    """Один браузер на всё время настройки, команды выполняются в своём потоке."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cmds: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self.picked: list[dict[str, Any]] = []
        self.picking = False
        self.url = ""
        self.open = False
        self.last_error = ""

    # ---------- публичный API ----------

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        self._wait_ready()

    def _wait_ready(self, seconds: float = 20) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            with self._lock:
                if self._pw is not None or self.last_error:
                    return
            time.sleep(0.05)

    def _cmd(self, name: str, **kw: Any) -> dict[str, Any]:
        self._ensure_thread()
        box: dict[str, Any] = {"done": threading.Event(), "result": None}
        self._cmds.put((name, kw, box))
        if not box["done"].wait(120):
            return {"ok": False, "error": "таймаут команды браузера"}
        return box["result"] or {"ok": False, "error": "нет ответа"}

    def launch(self, url: str, headless: bool = False) -> dict[str, Any]:
        return self._cmd("open", url=url, headless=headless)

    def start_pick(self) -> dict[str, Any]:
        return self._cmd("start_pick")

    def stop_pick(self) -> dict[str, Any]:
        return self._cmd("stop_pick")

    def probe(self, selector: str) -> dict[str, Any]:
        return self._cmd("probe", selector=selector)

    def screenshot(self) -> dict[str, Any]:
        return self._cmd("screenshot")

    def click_at(self, x: float, y: float) -> dict[str, Any]:
        return self._cmd("click_at", x=x, y=y)

    def scroll(self, dy: int) -> dict[str, Any]:
        return self._cmd("scroll", dy=dy)

    def goto(self, url: str) -> dict[str, Any]:
        return self._cmd("goto", url=url)

    def close(self) -> dict[str, Any]:
        return self._cmd("close")

    def state(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "picking": self.picking,
            "url": self.url,
            "picked": list(self.picked),
            "error": self.last_error,
        }

    # ---------- поток браузера ----------

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            self._pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Playwright не запустился: {exc}"
            return

        while True:
            name, kw, box = self._cmds.get()
            try:
                if name == "open":
                    box["result"] = self._do_open(kw["url"], bool(kw.get("headless")))
                elif name == "goto":
                    if not self.page:
                        box["result"] = {"ok": False, "error": "браузер не открыт"}
                    else:
                        self.page.goto(kw["url"], wait_until="domcontentloaded")
                        self.url = self.page.url
                        box["result"] = {"ok": True, "url": self.url}
                elif name == "start_pick":
                    box["result"] = self._do_pick()
                elif name == "stop_pick":
                    self._inject()
                    self.page.evaluate("window.__afPickOn = false")
                    self.picking = False
                    box["result"] = {"ok": True, "picked": list(self.picked)}
                elif name == "click_at":
                    box["result"] = self._do_click_at(kw["x"], kw["y"])
                elif name == "scroll":
                    self.page.evaluate("(dy) => window.scrollBy(0, dy)", int(kw["dy"]))
                    box["result"] = {"ok": True}
                elif name == "probe":
                    box["result"] = self._do_probe(kw["selector"])
                elif name == "screenshot":
                    box["result"] = self._do_screenshot()
                elif name == "close":
                    self._do_close()
                    box["result"] = {"ok": True}
                    box["done"].set()
                    break
                else:
                    box["result"] = {"ok": False, "error": "неизвестная команда"}
            except Exception as exc:  # noqa: BLE001
                box["result"] = {"ok": False, "error": str(exc).splitlines()[0][:400]}
            box["done"].set()

        try:
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass

    def _do_open(self, url: str, headless: bool) -> dict[str, Any]:
        self._do_close()
        self.last_error = ""
        self.picked = []
        try:
            self._browser = self._pw.chromium.launch(**launch_options(headless))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "не удалось запустить браузер. Установите его командой: "
                "playwright install chromium (" + str(exc).splitlines()[0][:200] + ")"
            ) from exc
        self._context = self._browser.new_context(viewport={"width": 1366, "height": 900})
        self._context.expose_binding("__afEmit", lambda source, info: self._on_pick(info))
        self.page = self._context.new_page()
        self.page.on("console", lambda m: None)
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.url = self.page.url
        self.open = True
        self.picking = False
        return {"ok": True, "url": self.url, "title": self.page.title()}

    def _do_close(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
        self._browser = None
        self._context = None
        self.page = None
        self.open = False
        self.picking = False

    def _inject(self) -> None:
        if not self.page:
            return
        try:
            self.page.evaluate(OVERLAY)
        except Exception:  # noqa: BLE001
            pass

    def _on_pick(self, info: Any) -> None:
        if not isinstance(info, dict):
            return
        item = {
            "selector": str(info.get("selector") or ""),
            "title": str(info.get("title") or info.get("label") or ""),
            "label": str(info.get("label") or ""),
            "tag": str(info.get("tag") or ""),
            "type": str(info.get("type") or ""),
        }
        if item["selector"] and item not in self.picked:
            self.picked.append(item)

    def _do_pick(self) -> dict[str, Any]:
        self._inject()
        self.page.evaluate("window.__afPickOn = true")
        self.picking = True
        return {"ok": True, "picked": list(self.picked)}

    def _do_probe(self, selector: str) -> dict[str, Any]:
        self._inject()
        info = self.page.evaluate(
            """(sel) => {
                let nodes = [];
                try { nodes = Array.from(document.querySelectorAll(sel)); } catch (e) {
                    return { error: 'некорректный селектор: ' + e.message };
                }
                const labels = nodes.slice(0, 5).map((el) => {
                    const t = (el.innerText || el.value || el.placeholder || '').trim();
                    return (el.nodeName.toLowerCase() + (t ? ': ' + t.slice(0, 40) : ''));
                });
                if (nodes[0]) {
                    nodes[0].scrollIntoView({block: 'center'});
                    nodes[0].style.outline = '3px solid #f59e0b';
                    setTimeout(() => { nodes[0].style.outline = ''; }, 1500);
                }
                return { count: nodes.length, labels };
            }""",
            selector,
        )
        return {"ok": True, **(info or {})}

    def _do_click_at(self, x: float, y: float) -> dict[str, Any]:
        self._inject()
        info = self.page.evaluate(
            """([x, y, cssPathSrc]) => {
                const cssPath = (el) => {
                  if (!(el instanceof Element)) return '';
                  if (el.id && /^[A-Za-z][\w\-]*$/.test(el.id)) return '#' + el.id;
                  const parts = []; let node = el; let depth = 0;
                  while (node && node.nodeType === 1 && depth < 6) {
                    let part = node.nodeName.toLowerCase();
                    if (node.id && /^[A-Za-z][\w\-]*$/.test(node.id)) { parts.unshift('#' + node.id); break; }
                    const name = node.getAttribute('name');
                    const itype = node.getAttribute('type');
                    if (name && ['input','select','textarea','button'].includes(part)) {
                      part += '[name="' + name + '"]';
                      if (itype && part === 'input') part += '[type="' + itype + '"]';
                      parts.unshift(part); break;
                    }
                    if (node.parentElement) {
                      const same = Array.from(node.parentElement.children).filter((c) => c.nodeName === node.nodeName);
                      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
                    }
                    parts.unshift(part); node = node.parentElement; depth += 1;
                  }
                  return parts.join(' > ');
                };
                const hit = document.elementFromPoint(x, y);
                if (!hit) return { error: 'в этой точке нет элемента' };
                const interactive = ['INPUT','SELECT','TEXTAREA','BUTTON','A'];
                let target = hit;
                if (!interactive.includes(target.nodeName)) {
                  target = hit.querySelector('input, select, textarea, button, a');
                  if (!target) {
                    const lab = hit.closest('label');
                    if (lab) target = lab.querySelector('input, select, textarea');
                  }
                  if (!target) {
                    return { error: 'в этой точке нет поля — кликните точнее по самому полю или кнопке' };
                  }
                }
                const friendly = (el) => {
                  let t = '';
                  if (el.labels && el.labels[0]) t = el.labels[0].innerText || '';
                  if (!t) t = el.getAttribute('aria-label') || '';
                  if (!t) t = el.getAttribute('placeholder') || '';
                  if (!t) t = el.getAttribute('title') || '';
                  if (!t) t = el.name || el.id || '';
                  if (['BUTTON', 'A'].includes(el.nodeName)) {
                    const txt = (el.innerText || '').trim();
                    if (txt) t = txt;
                  }
                  return t.trim().replace(/\s+/g, ' ').slice(0, 50);
                };
                const title = friendly(target);
                target.style.outline = '3px solid #f59e0b';
                setTimeout(() => { target.style.outline = ''; }, 1200);
                return {
                  selector: cssPath(target),
                  title: title,
                  label: target.nodeName.toLowerCase() + ' ' + (title || ''),
                  tag: target.nodeName.toLowerCase(),
                  type: target.type || '',
                };
            }""",
            [x, y, True],
        )
        info = info or {}
        if info.get("selector"):
            self._on_pick(info)
        return {"ok": True, **info}

    def _do_screenshot(self) -> dict[str, Any]:
        self._inject()
        png = self.page.screenshot(type="png")
        meta = self.page.evaluate(
            """() => ({ width: window.innerWidth, height: window.innerHeight,
                       offsetX: window.scrollX, offsetY: window.scrollY })"""
        )
        return {
            "ok": True,
            "image": "data:image/png;base64," + base64.b64encode(png).decode(),
            "url": self.page.url,
            "title": self.page.title(),
            **meta,
        }


picker = Picker()
