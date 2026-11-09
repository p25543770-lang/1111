/* Локальный ИИ-ассистент: чат + управление приложением */

(function () {
  "use strict";

  const log = document.getElementById("chatLog");
  const empty = document.getElementById("chatEmpty");
  const form = document.getElementById("chatForm");
  const input = document.getElementById("chatInput");
  const sendBtn = document.getElementById("chatSend");
  const badge = document.getElementById("aiState");
  const hint = document.getElementById("aiHint");
  const refreshBtn = document.getElementById("aiRefresh");

  if (!log || !form || !input) return;

  const history = [];
  let busy = false;
  let lastStatus = null;

  const ACTION_LABELS = {
    set_url: "АДРЕС",
    open_site: "ОТКРЫТЬ САЙТ",
    load_demo: "ДЕМО",
    parse_data: "ДАННЫЕ",
    add_steps: "ШАГИ",
    clear_steps: "ОЧИСТИТЬ ШАГИ",
    start_run: "ЗАПУСК",
    stop_run: "СТОП",
    open_panel: "РАЗДЕЛ",
  };

  /* ---------------------------------------------------------- контекст */

  function buildContext() {
    return {
      url: (document.getElementById("url") || {}).value || "",
      rows: (typeof state !== "undefined" && state.rows ? state.rows.length : 0),
      columns: (typeof state !== "undefined" && state.headers) || [],
      steps: (typeof state !== "undefined" && state.steps ? state.steps : []).map((s) => ({
        selector: s.selector,
        action: s.action,
        value_mode: s.value_mode,
        column: s.column,
        value: s.value,
      })),
      running: typeof state !== "undefined" ? !!state.running : false,
      site_open: typeof state !== "undefined" ? !!state.siteOpen : false,
      picking: typeof state !== "undefined" ? !!state.picking : false,
      panel: window.__vv && window.__vv.current ? window.__vv.current() : "site",
    };
  }

  /* ---------------------------------------------------------- сообщения */

  function scrollDown() {
    log.scrollTop = log.scrollHeight;
  }

  function addMessage(role, text, actions, pending) {
    if (empty) empty.remove();

    const node = document.createElement("div");
    node.className = "msg " + role + (pending ? " pending" : "");

    const roleNode = document.createElement("div");
    roleNode.className = "msg-role";
    roleNode.textContent = role === "user" ? "ВЫ" : "ЛОКАЛЬНЫЙ ИИ";

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = text;

    node.append(roleNode, body);

    if (actions && actions.length) {
      const chips = document.createElement("div");
      chips.className = "msg-actions";
      actions.forEach((action) => {
        const chip = document.createElement("span");
        chip.className = "msg-action";
        chip.dataset.type = action.type;
        chip.textContent = ACTION_LABELS[action.type] || action.type;
        chips.appendChild(chip);
      });
      node.appendChild(chips);
    }

    log.appendChild(node);
    scrollDown();
    return node;
  }

  function markAction(node, type, result) {
    if (!node) return;
    const chip = node.querySelector(`.msg-action[data-type="${type}"]`);
    if (chip) {
      chip.textContent = (result.ok ? "✓ " : "✕ ") + chip.textContent.replace(/^[✓✕]\s*/, "");
      if (!result.ok) chip.style.borderColor = "#fff";
    }
    if (result.note) {
      let box = node.querySelector(".msg-actions");
      if (!box) {
        box = document.createElement("div");
        box.className = "msg-actions";
        node.appendChild(box);
      }
      const note = document.createElement("span");
      note.className = "msg-action note";
      note.textContent = result.note;
      box.appendChild(note);
    }
  }

  /* ---------------------------------------------------------- действия */

  const ACTIONS = {
    async set_url(a) {
      document.getElementById("url").value = a.url;
      if (typeof saveLocal === "function") saveLocal();
      return { ok: true, note: "адрес: " + a.url };
    },

    async open_site(a) {
      if (a.url) {
        document.getElementById("url").value = a.url;
        if (typeof saveLocal === "function") saveLocal();
      }
      await openSite();
      if (window.__vv) window.__vv.activate("site");
      return { ok: true, note: "сайт открыт внутри приложения" };
    },

    async load_demo() {
      await loadDemo();
      return { ok: true, note: "демо-данные и шаги загружены" };
    },

    async parse_data(a) {
      const data = await post("/api/data/text", {
        text: a.text,
        has_header: a.has_header !== false,
      });
      setTable(data);
      return { ok: true, note: `строк: ${data.rows.length}, колонок: ${data.headers.length}` };
    },

    async add_steps(a) {
      const added = [];
      (a.steps || []).forEach((raw) => {
        const step = newStep();
        step.selector = raw.selector || "";
        step.action = raw.action || "fill";
        step.value_mode = raw.value_mode || (raw.column ? "column" : "fixed");
        step.column = raw.column || "";
        step.value = raw.value || "";
        step.title = raw.title || raw.column || step.selector || "шаг";
        state.steps.push(step);
        added.push(step);
      });
      if (!added.length) return { ok: false, note: "не указаны селекторы" };
      renderSteps();
      if (typeof saveLocal === "function") saveLocal();
      return { ok: true, note: `добавлено шагов: ${added.length}` };
    },

    async clear_steps() {
      state.steps = [];
      state.activeStep = null;
      renderSteps();
      if (typeof saveLocal === "function") saveLocal();
      return { ok: true, note: "список шагов очищен" };
    },

    async start_run() {
      const problem = typeof validate === "function" ? validate() : null;
      if (problem) return { ok: false, note: problem };
      await startRun();
      window.__vv && window.__vv.activate("run");
      return { ok: true, note: "сценарий запущен" };
    },

    async stop_run() {
      await stopRun();
      return { ok: true, note: "остановлено" };
    },

    async open_panel(a) {
      if (!window.__vv) return { ok: false, note: "навигация недоступна" };
      window.__vv.activate(a.panel);
      return { ok: true, note: "открыт раздел «" + a.panel + "»" };
    },
  };

  async function applyActions(node, actions) {
    for (const action of actions) {
      const handler = ACTIONS[action.type];
      if (!handler) {
        markAction(node, action.type, { ok: false, note: "неизвестное действие" });
        continue;
      }
      try {
        const result = await handler(action);
        markAction(node, action.type, result || { ok: true });
      } catch (e) {
        markAction(node, action.type, { ok: false, note: e.message || "ошибка" });
      }
    }
  }

  /* ---------------------------------------------------------- статус */

  function renderStatus(info) {
    lastStatus = info;
    if (!badge) return;

    if (!info) {
      badge.textContent = "нет связи";
      badge.className = "badge err";
      return;
    }

    badge.textContent = info.label || info.mode;
    badge.className = "badge" + (info.mode === "offline" ? "" : " on");

    if (hint) {
      if (info.mode === "offline") {
        hint.textContent =
          "Локальная модель не найдена — работает встроенный офлайн-режим (команды, данные, шаги, запуск). " +
          "Чтобы подключить настоящую модель, запустите Ollama с моделью на 127.0.0.1:11434 или LM Studio " +
          "и укажите адрес в AUTOVVOD_AI_URL.";
      } else {
        hint.textContent =
          "Модель: " + (info.label || info.model) + ". Сервер: " + info.endpoint +
          ". Запросы не покидают это устройство.";
      }
    }
  }

  async function refreshStatus() {
    if (badge) {
      badge.textContent = "проверяю…";
      badge.className = "badge";
    }
    try {
      renderStatus(await api("/api/ai/status"));
    } catch (e) {
      renderStatus(null);
    }
  }

  /* ---------------------------------------------------------- отправка */

  async function send(text) {
    const message = (text || "").trim();
    if (!message || busy) return;

    busy = true;
    sendBtn.disabled = true;
    input.value = "";
    addMessage("user", message);
    history.push({ role: "user", content: message });

    const pending = addMessage("assistant", "думаю…", [], true);

    try {
      const res = await post("/api/ai/chat", {
        messages: history.slice(-10),
        context: buildContext(),
      });
      pending.remove();
      renderStatus(res);
      const node = addMessage("assistant", res.say || "Готово.", res.actions || []);
      history.push({ role: "assistant", content: res.say || "" });
      await applyActions(node, res.actions || []);
    } catch (e) {
      pending.remove();
      addMessage("assistant", "Локальный ассистент недоступен: " + (e.message || e), []);
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  /* ---------------------------------------------------------- события */

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    send(input.value);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  document.querySelectorAll("[data-ai-prompt]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (busy) return;
      send(chip.dataset.aiPrompt);
    });
  });

  if (refreshBtn) refreshBtn.addEventListener("click", refreshStatus);

  const aiNav = document.querySelector('.nav-item[data-panel="ai"]');
  if (aiNav) {
    aiNav.addEventListener("click", () => {
      if (!lastStatus) refreshStatus();
      setTimeout(() => input.focus(), 250);
    });
  }

  refreshStatus();
})();
