/* АВТОВВОД — навигация приложения и зеркало состояния */

(function () {
  "use strict";

  const PANELS = {
    site: "САЙТ",
    data: "ДАННЫЕ",
    steps: "ШАГИ",
    run: "ЗАПУСК",
  };
  const ORDER = ["site", "data", "steps", "run"];
  const STORE_KEY = "autoform.panel";

  const navButtons = Array.from(document.querySelectorAll(".nav-item"));
  const panels = Array.from(document.querySelectorAll(".panel"));
  const crumb = document.getElementById("crumb");

  let current = "site";

  function activate(name, opts) {
    const options = opts || {};
    if (!PANELS[name]) name = "site";
    current = name;

    navButtons.forEach((b) => b.classList.toggle("is-active", b.dataset.panel === name));
    panels.forEach((p) => p.classList.toggle("is-active", p.dataset.panel === name));
    if (crumb) crumb.textContent = PANELS[name];

    try { localStorage.setItem(STORE_KEY, name); } catch (e) { /* хранилище недоступно */ }
    try { history.replaceState(null, "", "#" + name); } catch (e) { /* не критично */ }

    if (options.scroll !== false) {
      window.scrollTo({ top: 0, behavior: options.instant ? "auto" : "smooth" });
    }
  }

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => activate(btn.dataset.panel));
  });

  window.addEventListener("hashchange", () => {
    const name = (location.hash || "").replace("#", "");
    if (PANELS[name] && name !== current) activate(name, { scroll: false });
  });

  document.addEventListener("keydown", (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const i = ["1", "2", "3", "4"].indexOf(e.key);
    if (i === -1) return;
    e.preventDefault();
    activate(ORDER[i]);
  });

  // стартовый раздел: hash → сохранённый → первый
  const fromHash = (location.hash || "").replace("#", "");
  let initial = PANELS[fromHash] ? fromHash : null;
  if (!initial) {
    try {
      const saved = localStorage.getItem(STORE_KEY);
      if (saved && PANELS[saved]) initial = saved;
    } catch (e) { /* пусто */ }
  }
  activate(initial || "site", { scroll: false, instant: true });

  /* ------------------------------------------------ зеркало состояния */

  const chip = document.getElementById("globalState");
  const runBadge = document.getElementById("runState");

  const CHIP_BY_TEXT = [
    ["работает", "running"],
    ["готово", "done"],
    ["остановлено", "stopped"],
    ["ошибка", "error"],
  ];

  function syncChip() {
    if (!chip || !runBadge) return;
    const text = (runBadge.textContent || "").trim().toLowerCase();
    const match = CHIP_BY_TEXT.find((pair) => text.indexOf(pair[0]) === 0);
    chip.className = "run-chip " + (match ? match[1] : "idle");
    chip.textContent = text || "ожидание";
  }

  if (runBadge) {
    new MutationObserver(syncChip).observe(runBadge, {
      childList: true,
      characterData: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    syncChip();
  }

  if (chip) {
    chip.style.cursor = "pointer";
    chip.title = "Открыть раздел «Запуск»";
    chip.addEventListener("click", () => activate("run"));
  }

  /* ------------------------------------------------ счётчики в навигации */

  const counters = {
    site: document.getElementById("countSite"),
    data: document.getElementById("countData"),
    steps: document.getElementById("countSteps"),
    run: document.getElementById("countRun"),
  };

  function watch(id, handler) {
    const node = document.getElementById(id);
    if (!node) return;
    const run = () => handler(node.textContent || "");
    new MutationObserver(run).observe(node, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    run();
  }

  watch("tableInfo", (text) => {
    const m = text.match(/Строк:\s*(\d+)/i);
    if (counters.data) counters.data.textContent = m ? m[1] : "0";
  });

  watch("stepsHint", (text) => {
    const m = text.match(/Шагов:\s*(\d+)/i);
    if (counters.steps) counters.steps.textContent = m ? m[1] : "0";
  });

  watch("runCount", (text) => {
    if (!counters.run) return;
    const m = text.match(/(\d+)\s+из\s+(\d+)/i);
    if (m && Number(m[2]) > 0) {
      counters.run.textContent = Math.round((Number(m[1]) / Number(m[2])) * 100) + "%";
    } else {
      counters.run.textContent = "0%";
    }
  });

  watch("siteState", (text) => {
    if (!counters.site) return;
    const t = text.toLowerCase();
    if (t.indexOf("идёт выбор") === 0) counters.site.textContent = "ВЫБОР";
    else if (t.indexOf("сайт открыт") === 0) counters.site.textContent = "ON";
    else counters.site.textContent = "—";
  });

  /* ------------------------------------------------ быстрые действия */

  const demoTop = document.getElementById("btnDemoTop");
  if (demoTop) {
    demoTop.addEventListener("click", () => {
      activate("site");
      if (typeof window.loadDemo === "function") window.loadDemo();
    });
  }

  // запуск всегда показывает журнал
  const start = document.getElementById("btnStart");
  if (start) start.addEventListener("click", () => activate("run"));
})();
