/* АвтоВвод — интерфейс */

const el = (id) => document.getElementById(id);
const state = {
  url: "",
  headers: [],
  rows: [],
  steps: [],
  preSteps: [],
  picked: [],
  running: false,
  activeStep: null,
  siteOpen: false,
  picking: false,
  preview: null, // {image, offsetX, offsetY, width, height}
  stepIds: [],
};

/* ------------------------------------------------------------ утилиты */

function toast(msg, kind = "info") {
  const box = document.createElement("div");
  box.textContent = msg;
  box.className = kind === "err" ? "toast toast-err" : "toast";
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 3000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.raw)) || `ошибка ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

const uid = () => Math.random().toString(36).slice(2, 9);

/* ------------------------------------------------------------ данные */

function renderTable() {
  const wrap = el("tableWrap");
  const table = el("dataTable");
  table.innerHTML = "";
  if (!state.rows.length) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  el("tableInfo").textContent = `Строк: ${state.rows.length} · Колонок: ${state.headers.length}`;

  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  const th0 = document.createElement("th");
  th0.textContent = "№";
  hr.appendChild(th0);
  state.headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    const sub = document.createElement("small");
    const used = state.steps.filter((s) => s.value_mode !== "fixed" && s.column === h).length;
    sub.textContent = used ? `используется в ${used} шаг.` : "→ в шаг";
    th.appendChild(sub);
    th.onclick = () => assignColumn(h);
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  state.rows.slice(0, 200).forEach((row, i) => {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.className = "num";
    td0.textContent = i + 1;
    tr.appendChild(td0);
    state.headers.forEach((_, ci) => {
      const td = document.createElement("td");
      td.textContent = row[ci] ?? "";
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  if (state.rows.length > 200) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = state.headers.length + 1;
    td.className = "muted";
    td.textContent = `…показаны первые 200 строк из ${state.rows.length}`;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function setTable(data) {
  state.headers = data.headers || [];
  state.rows = data.rows || [];
  renderTable();
  renderSteps();
  saveLocal();
}

async function parsePaste() {
  const text = el("paste").value;
  if (!text.trim()) { toast("Вставьте данные из таблицы", "err"); return; }
  try {
    const data = await post("/api/data/text", { text, has_header: el("hasHeader").checked });
    setTable(data);
    el("pasteInfo").textContent = `принято ${data.rows.length} строк, ${data.headers.length} колонок`;
    toast(`Загружено ${data.rows.length} строк`);
  } catch (e) { toast(e.message, "err"); }
}

async function parseManual() {
  const text = el("manual").value;
  if (!text.trim()) { toast("Введите строки", "err"); return; }
  const cols = el("manualHeaders").value.trim();
  try {
    const data = await post("/api/data/text", { text, has_header: false });
    if (cols) {
      const names = cols.split(",").map((s) => s.trim()).filter(Boolean);
      data.headers = data.headers.map((h, i) => names[i] || h);
    }
    setTable(data);
    el("manualInfo").textContent = `принято ${data.rows.length} строк`;
    toast(`Загружено ${data.rows.length} строк`);
  } catch (e) { toast(e.message, "err"); }
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await api(`/api/data/file?has_header=${el("hasHeader").checked}`, { method: "POST", body: fd });
    setTable(data);
    toast(`Загружено ${data.rows.length} строк из файла`);
  } catch (e) { toast(e.message, "err"); }
}

/* ------------------------------------------------------------ шаги */

const ACTIONS = [
  ["fill", "Ввести текст"],
  ["type", "Печатать по буквам"],
  ["click", "Клик"],
  ["select", "Выбрать в списке"],
  ["check", "Поставить галочку"],
  ["uncheck", "Снять галочку"],
  ["press", "Нажать клавишу"],
  ["upload", "Загрузить файл"],
  ["wait_for", "Дождаться элемента"],
  ["wait", "Пауза"],
  ["goto", "Перейти по ссылке"],
];

const VALUE_MODES = [
  ["column", "из колонки"],
  ["fixed", "свой текст"],
  ["template", "шаблон {{колонка}}"],
];

function newStep(pre = false) {
  return {
    id: uid(),
    title: pre ? "Подготовка" : "Поле",
    selector: "",
    action: "fill",
    value_mode: state.headers.length ? "column" : "fixed",
    column: state.headers[0] || "",
    value: "",
    delay_ms: 0,
    optional: false,
  };
}

function renderSteps() {
  renderStepList(el("stepsBody"), state.steps, false);
  renderStepList(el("preBody"), state.preSteps, true);
  updateRunAvailability();
  el("stepsHint").innerHTML = state.steps.length
    ? `Шагов: <b>${state.steps.length}</b>. Для каждого шага укажите поле на сайте (селектор) и откуда брать значение.`
    : "Каждый шаг — это поле на сайте и значение из колонки таблицы. Порядок шагов = порядок действий браузера.";
}

function renderStepList(tbody, list, isPre) {
  tbody.innerHTML = "";
  if (!list.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.className = "muted";
    td.style.padding = "10px 6px";
    td.textContent = isPre
      ? "Нет подготовительных шагов (необязательно)"
      : "Пока нет шагов. Нажмите «+ Шаг» или выбирайте поля прямо на сайте.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  list.forEach((step, i) => {
    const tr = document.createElement("tr");
    tr.onclick = () => {
      if (isPre) return;
      state.activeStep = i;
      markActive();
      renderSteps();
    };

    const tdIdx = document.createElement("td");
    tdIdx.className = "idx";
    tdIdx.textContent = i + 1;
    tr.appendChild(tdIdx);

    const tdSel = document.createElement("td");
    const box = document.createElement("div");
    box.style.cssText = "display:flex;gap:6px";
    const inpSel = document.createElement("input");
    inpSel.type = "text";
    inpSel.className = "sel";
    inpSel.placeholder = '#email или input[name="phone"]';
    inpSel.value = step.selector || "";
    inpSel.oninput = () => {
      step.selector = inpSel.value;
      inpSel.classList.remove("ok", "bad");
      if (!step.title || step.title === "Поле") step.title = "Шаг " + (i + 1);
      saveLocal();
    };
    const btnTest = document.createElement("button");
    btnTest.className = "tiny ghost";
    btnTest.textContent = "проверить";
    btnTest.title = "Подсветить элемент на открытом сайте";
    btnTest.onclick = async (ev) => {
      ev.stopPropagation();
      if (!state.siteOpen) { toast("Сначала откройте сайт в разделе 1", "err"); return; }
      try {
        const r = await post("/api/picker/probe", { selector: step.selector || "" });
        if (r.error) { inpSel.classList.add("bad"); toast(r.error, "err"); }
        else if (!r.count) { inpSel.classList.add("bad"); toast("Элемент не найден на странице", "err"); }
        else { inpSel.classList.add("ok"); toast(`Найдено элементов: ${r.count}`); }
        await takeShot();
      } catch (e) { toast(e.message, "err"); }
    };
    box.append(inpSel, btnTest);
    tdSel.appendChild(box);
    if (step.title && step.title !== "Поле") {
      const cap = document.createElement("div");
      cap.className = "muted";
      cap.style.cssText = "margin-top:3px;font-size:11.5px";
      cap.textContent = step.title;
      tdSel.appendChild(cap);
    }
    tr.appendChild(tdSel);

    const tdMode = document.createElement("td");
    const selMode = document.createElement("select");
    VALUE_MODES.forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      if ((step.value_mode || "column") === v) o.selected = true;
      selMode.appendChild(o);
    });
    selMode.onchange = () => { step.value_mode = selMode.value; saveLocal(); renderSteps(); };
    tdMode.appendChild(selMode);
    tr.appendChild(tdMode);

    const tdVal = document.createElement("td");
    const mode = step.value_mode || "column";
    if (mode === "column") {
      const sel = document.createElement("select");
      if (!state.headers.length) {
        const o = document.createElement("option");
        o.textContent = "сначала загрузите данные";
        sel.appendChild(o);
        sel.disabled = true;
      }
      state.headers.forEach((h) => {
        const o = document.createElement("option");
        o.value = h; o.textContent = h;
        if (step.column === h) o.selected = true;
        sel.appendChild(o);
      });
      if (!step.column && state.headers.length) step.column = state.headers[0];
      sel.onchange = () => { step.column = sel.value; saveLocal(); renderSteps(); };
      tdVal.appendChild(sel);
      if (state.rows.length && step.column) {
        const idx = state.headers.indexOf(step.column);
        const sample = document.createElement("div");
        sample.className = "muted";
        sample.style.marginTop = "3px";
        sample.textContent = "1-я строка: " + (state.rows[0][idx] || "—");
        tdVal.appendChild(sample);
      }
    } else {
      const inp = document.createElement("input");
      inp.type = "text";
      inp.placeholder = mode === "template" ? "например {{Имя}} — {{Город}}" : "текст";
      inp.value = step.value || "";
      inp.oninput = () => { step.value = inp.value; saveLocal(); };
      tdVal.appendChild(inp);
      if (mode === "template" && state.rows.length) {
        const sample = document.createElement("div");
        sample.className = "muted";
        sample.style.marginTop = "3px";
        const first = state.rows[0];
        sample.textContent = "1-я строка: " + String(step.value || "").replace(
          /\{\{\s*([^{}]+?)\s*\}\}/g,
          (m, name) => {
            const idx = state.headers.indexOf(name.trim());
            return idx >= 0 ? first[idx] : m;
          },
        );
        tdVal.appendChild(sample);
      }
    }
    tr.appendChild(tdVal);

    const tdAct = document.createElement("td");
    tdAct.className = "col-act";
    const selAct = document.createElement("select");
    ACTIONS.forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      if (step.action === v) o.selected = true;
      selAct.appendChild(o);
    });
    selAct.onchange = () => { step.action = selAct.value; saveLocal(); renderSteps(); };
    tdAct.appendChild(selAct);
    tr.appendChild(tdAct);

    const tdDelay = document.createElement("td");
    tdDelay.className = "col-pause";
    const inpDelay = document.createElement("input");
    inpDelay.type = "number";
    inpDelay.min = "0";
    inpDelay.step = "100";
    inpDelay.style.width = "70px";
    inpDelay.value = step.delay_ms || 0;
    inpDelay.title = "Пауза после шага, мс";
    inpDelay.oninput = () => { step.delay_ms = Number(inpDelay.value) || 0; saveLocal(); };
    tdDelay.appendChild(inpDelay);
    tr.appendChild(tdDelay);

    const tdCtrl = document.createElement("td");
    tdCtrl.style.whiteSpace = "nowrap";
    const mk = (txt, title, fn) => {
      const b = document.createElement("button");
      b.className = "tiny ghost";
      b.textContent = txt;
      b.title = title;
      b.onclick = (ev) => { ev.stopPropagation(); fn(); };
      return b;
    };
    tdCtrl.append(
      mk("↑", "выше", () => { if (i > 0) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; saveLocal(); renderSteps(); } }),
      mk("↓", "ниже", () => { if (i < list.length - 1) { [list[i + 1], list[i]] = [list[i], list[i + 1]]; saveLocal(); renderSteps(); } }),
      mk("✕", "удалить", () => { list.splice(i, 1); saveLocal(); renderSteps(); }),
    );
    tr.appendChild(tdCtrl);

    if (!isPre && state.activeStep === i) tr.style.background = "rgba(34,211,238,.07)";
    tbody.appendChild(tr);
  });
}

function markActive() {
  renderSteps();
}

function assignColumn(name) {
  if (state.activeStep === null || !state.steps[state.activeStep]) {
    if (state.steps.length === 0) {
      const s = newStep();
      s.column = name;
      s.value_mode = "column";
      state.steps.push(s);
      state.activeStep = 0;
      renderSteps();
      saveLocal();
      toast(`Создан шаг с колонкой «${name}» — укажите поле на сайте`);
      return;
    }
    openStepPicker(name);
    return;
  }
  const s = state.steps[state.activeStep];
  s.column = name;
  s.value_mode = "column";
  renderSteps();
  saveLocal();
  toast(`Колонка «${name}» → шаг ${state.activeStep + 1}`);
}

function openStepPicker(column) {
  const body = el("modalBody");
  el("modalTitle").textContent = `Куда вставлять колонку «${column}»?`;
  body.innerHTML = "";
  const list = document.createElement("div");
  list.className = "modal-list";
  state.steps.forEach((s, i) => {
    const item = document.createElement("div");
    item.className = "item";
    const label = document.createElement("span");
    label.textContent = `Шаг ${i + 1}: ${s.selector || "(поле не выбрано)"}`;
    const btn = document.createElement("button");
    btn.className = "tiny primary";
    btn.textContent = "Выбрать";
    btn.onclick = () => {
      state.steps[i].column = column;
      state.steps[i].value_mode = "column";
      closeModal();
      renderSteps();
      saveLocal();
    };
    item.append(label, btn);
    list.appendChild(item);
  });
  const add = document.createElement("button");
  add.className = "ghost";
  add.style.marginTop = "10px";
  add.textContent = "+ Новый шаг с этой колонкой";
  add.onclick = () => {
    const s = newStep();
    s.column = column;
    s.value_mode = "column";
    state.steps.push(s);
    state.activeStep = state.steps.length - 1;
    closeModal();
    renderSteps();
    saveLocal();
  };
  body.append(list, add);
  el("modal").classList.remove("hidden");
}

/* ------------------------------------------------------------ сайт, превью, выбор полей */

function setSiteBadge() {
  const b = el("siteState");
  if (state.picking) { b.textContent = "идёт выбор полей — кликайте по превью ниже"; b.className = "badge busy"; }
  else if (state.siteOpen) { b.textContent = "сайт открыт"; b.className = "badge on"; }
  else { b.textContent = "браузер не открыт"; b.className = "badge"; }
  el("btnPick").disabled = !state.siteOpen || state.picking;
  el("btnPickDone").disabled = !state.picking;
  el("btnShot").disabled = !state.siteOpen;
  el("btnPick").textContent = state.picking ? "Выбор идёт…" : "Выбирать поля на сайте";
  el("pickHint").classList.toggle("hidden", !state.picking);
  el("thumb").style.cursor = state.picking ? "crosshair" : "default";
}

async function openSite() {
  const url = el("url").value.trim();
  if (!url) { toast("Укажите адрес сайта", "err"); return; }
  const btn = el("btnOpenSite");
  btn.disabled = true;
  btn.textContent = "Открываю…";
  try {
    const r = await post("/api/picker/open", { url, headless: !el("optPickWindow").checked });
    state.siteOpen = true;
    state.picked = [];
    el("url").value = r.url || url;
    toast("Сайт открыт. Нажмите «Выбирать поля на сайте»");
    setSiteBadge();
    await takeShot();
    saveLocal();
  } catch (e) {
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Открыть сайт";
  }
}

async function takeShot() {
  try {
    const r = await api("/api/picker/screenshot");
    state.preview = {
      image: r.image,
      offsetX: r.offsetX || 0,
      offsetY: r.offsetY || 0,
      width: r.width || 1366,
      height: r.height || 900,
    };
    el("thumb").src = r.image;
    el("thumbCap").textContent = `${r.title || ""} — ${r.url || ""}`;
    el("thumbWrap").classList.remove("hidden");
  } catch (e) { /* снимок не критичен */ }
}

let pickTimer = null;

async function startPick() {
  try {
    await post("/api/picker/pick/start");
    state.picking = true;
    state.picked = [];
    setSiteBadge();
    clearInterval(pickTimer);
    pickTimer = setInterval(pollPicked, 800);
    toast("Кликайте по нужным полям на превью страницы");
  } catch (e) { toast(e.message, "err"); }
}

async function stopPick() {
  clearInterval(pickTimer);
  pickTimer = null;
  try {
    const r = await post("/api/picker/pick/stop");
    state.picking = false;
    state.picked = r.picked || [];
    setSiteBadge();
    applyPicked();
    await takeShot();
  } catch (e) { toast(e.message, "err"); }
}

async function pollPicked() {
  try {
    const r = await api("/api/picker/picked");
    if ((r.picked || []).length !== state.picked.length) {
      state.picked = r.picked || [];
      applyPicked();
      await takeShot();
    }
  } catch (e) { /* игнорируем */ }
}

function applyPicked() {
  const existing = new Set(state.steps.map((s) => s.selector));
  let added = 0;
  state.picked.forEach((p) => {
    if (!p.selector || existing.has(p.selector)) return;
    const step = newStep();
    step.selector = p.selector;
    step.title = p.title || p.label || "Поле";
    const tag = (p.tag || "").toLowerCase();
    if (tag === "button" || (tag === "input" && (p.type === "submit" || p.type === "button"))) {
      step.action = "click";
      step.value_mode = "fixed";
      step.value = "";
    } else if (tag === "select") {
      step.action = "select";
    } else if (tag === "input" && p.type === "checkbox") {
      step.action = "check";
      step.value_mode = "fixed";
    }
    if (state.headers.length && ["fill", "type", "select"].includes(step.action)) {
      const candidates = state.headers.filter(
        (h) => !state.steps.some((s) => s.column === h && s.value_mode === "column"),
      );
      step.column = candidates[0] || state.headers[Math.min(added, state.headers.length - 1)];
      step.value_mode = "column";
    }
    state.steps.push(step);
    existing.add(p.selector);
    added += 1;
  });
  if (added) {
    state.activeStep = state.steps.length - 1;
    renderSteps();
    saveLocal();
    toast(`Добавлено шагов: ${added}. Проверьте, из какой колонки брать значения.`);
  }
}

function thumbClick(ev) {
  const img = el("thumb");
  if (!state.picking || !state.preview) return;
  const rect = img.getBoundingClientRect();
  const scale = rect.width / state.preview.width;
  const x = (ev.clientX - rect.left) / scale;
  const y = (ev.clientY - rect.top) / scale;
  post("/api/picker/click_at", { x, y })
    .then(async (r) => {
      if (r.selector) {
        state.picked = [...state.picked, { selector: r.selector, label: r.label, tag: r.tag, type: r.type }];
        applyPicked();
      }
      await takeShot();
    })
    .catch((e) => toast(e.message, "err"));
}

function thumbWheel(ev) {
  if (!state.preview) return;
  ev.preventDefault();
  const dy = ev.deltaY > 0 ? 500 : -500;
  post("/api/picker/scroll", { dy })
    .then(() => takeShot())
    .catch(() => {});
}

/* ------------------------------------------------------------ запуск */

function collectConfig() {
  const clean = (s) => ({
    title: s.title,
    selector: s.selector,
    action: s.action,
    value_mode: s.value_mode,
    column: s.column,
    value: s.value,
    delay_ms: Number(s.delay_ms) || 0,
    optional: !!s.optional,
  });
  return {
    url: el("url").value.trim(),
    headless: el("optHeadless").checked,
    reload_each_row: el("optReload").checked,
    stop_on_error: el("optStopOnError").checked,
    screenshot_on_error: el("optShot").checked,
    wait_after_row_ms: Number(el("optWait").value) || 0,
    repeat_count: Number(el("optRepeat").value) || 1,
    start_row: Math.max(0, (Number(el("optStart").value) || 1) - 1),
    steps: state.steps.map(clean),
    pre_steps: state.preSteps.map(clean),
  };
}

function validate() {
  if (!el("url").value.trim()) return "Укажите адрес сайта";
  if (!state.steps.length) return "Добавьте хотя бы один шаг";
  if (!state.rows.length) return "Загрузите данные из списка";
  for (let i = 0; i < state.steps.length; i += 1) {
    const s = state.steps[i];
    const where = `Шаг ${i + 1}`;
    if (["wait", "goto"].includes(s.action)) continue;
    if (!s.selector) return `${where}: не выбрано поле на сайте`;
    const needsValue = ["fill", "type", "select", "press", "upload"].includes(s.action);
    if (!needsValue) continue;
    const mode = s.value_mode || "column";
    if (s.action !== "press" && s.action !== "upload") {
      if (mode === "column" && !s.column) return `${where}: не выбрана колонка`;
      if (mode === "template") {
        if (!String(s.value || "").trim()) return `${where}: не указан шаблон`;
        const names = [...String(s.value).matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((m) => m[1].trim());
        const bad = names.find((n) => !state.headers.includes(n));
        if (bad) return `${where}: в шаблоне нет колонки «${bad}»`;
      } else if (mode === "fixed" && s.action !== "press" && !String(s.value || "").trim()) {
        return `${where}: не указано значение`;
      }
    }
  }
  return null;
}

function updateRunAvailability() {
  el("btnStart").disabled = state.running;
  el("btnStop").disabled = !state.running;
  el("btnOpenSite").disabled = state.running;
}

async function startRun() {
  const err = validate();
  if (err) { toast(err, "err"); return; }
  el("log").innerHTML = "";
  el("bar").style.width = "0%";
  try {
    await post("/api/run", { config: collectConfig(), headers: state.headers, rows: state.rows });
    setRunState("running");
  } catch (e) { toast(e.message, "err"); }
}

async function stopRun() {
  try { await post("/api/stop"); } catch (e) { toast(e.message, "err"); }
}

function setRunState(s) {
  const map = {
    idle: ["ожидание", "badge"],
    running: ["работает…", "badge busy"],
    done: ["готово", "badge on"],
    stopped: ["остановлено", "badge err"],
    error: ["ошибка", "badge err"],
  };
  const [txt, cls] = map[s] || map.idle;
  const b = el("runState");
  b.textContent = txt;
  b.className = cls;
  state.running = s === "running";
  updateRunAvailability();
}

function logLine(ev) {
  const box = el("log");
  const line = document.createElement("div");
  line.className = "ln";
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString("ru-RU");
  const m = document.createElement("span");
  m.className = ev.level || "info";
  m.textContent = ev.msg || "";
  line.append(t, m);
  const pinned = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.appendChild(line);
  if (pinned) box.scrollTop = box.scrollHeight;
}

function onEvent(ev) {
  if (ev.type === "log") logLine(ev);
  else if (ev.type === "state") setRunState(ev.state);
  else if (ev.type === "progress") {
    const pct = ev.total ? Math.round((ev.done / ev.total) * 100) : 0;
    el("bar").style.width = `${pct}%`;
    el("runCount").textContent = `обработано ${ev.done} из ${ev.total} (строка ${ev.row})`;
  }
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch { /* пропускаем */ }
  };
}

async function initStatus() {
  try {
    const s = await api("/api/status");
    setRunState(s.state || "idle");
    if (s.total) el("runCount").textContent = `обработано ${s.done} из ${s.total}`;
    const ps = await api("/api/picker/state");
    state.siteOpen = ps.open;
    state.picking = ps.picking;
    if (ps.url && !el("url").value) el("url").value = ps.url;
    setSiteBadge();
  } catch (e) { /* сервер мог только стартовать */ }
}

/* ------------------------------------------------------------ настройки */

function saveLocal() {
  const data = {
    url: el("url").value,
    steps: state.steps,
    preSteps: state.preSteps,
    headers: state.headers,
    rows: state.rows,
    opts: {
      headless: el("optHeadless").checked,
      reload: el("optReload").checked,
      wait: el("optWait").value,
      repeat: el("optRepeat").value,
      start: el("optStart").value,
      stopOnError: el("optStopOnError").checked,
      shot: el("optShot").checked,
    },
  };
  try { localStorage.setItem("autoform", JSON.stringify(data)); } catch { /* переполнение */ }
}

function loadLocal() {
  let raw = null;
  try { raw = localStorage.getItem("autoform"); } catch { return; }
  if (!raw) return;
  try {
    const d = JSON.parse(raw);
    el("url").value = d.url || "";
    state.steps = d.steps || [];
    state.preSteps = d.preSteps || [];
    state.headers = d.headers || [];
    state.rows = d.rows || [];
    if (d.opts) {
      el("optHeadless").checked = d.opts.headless !== false;
      el("optReload").checked = !!d.opts.reload;
      if (d.opts.wait != null) el("optWait").value = d.opts.wait;
      if (d.opts.repeat != null) el("optRepeat").value = d.opts.repeat;
      if (d.opts.start != null) el("optStart").value = d.opts.start;
      el("optStopOnError").checked = d.opts.stopOnError !== false;
      el("optShot").checked = d.opts.shot !== false;
    }
  } catch { /* повреждённое хранилище */ }
}

const DEMO_TEXT = [
  "Имя\tТелефон\tГород\tКомментарий",
  "Иван Петров\t+79990000001\tМосква\tпервая заявка",
  "Мария Сидорова\t+79990000002\tКазань\tперезвонить после 18",
  "Алексей Смирнов\t+79990000003\tНовосибирск\tинтересует тариф Про",
].join("\n");

async function loadDemo() {
  try {
    el("url").value = `${location.origin}/demo/form.html`;
    el("paste").value = DEMO_TEXT;
    const table = await post("/api/data/text", { text: DEMO_TEXT, has_header: true });
    setTable(table);
    const mk = (sel, title, action, column, mode = "column", value = "") => ({
      ...newStep(), selector: sel, title, action, value_mode: mode, column, value,
    });
    state.steps = [
      mk("#name", "Имя", "fill", "Имя"),
      mk("#phone", "Телефон", "fill", "Телефон"),
      mk("#city", "Город", "select", "Город"),
      mk("#comment", "Комментарий", "fill", "Комментарий"),
      mk("#agree", "согласие", "check", "", "fixed", ""),
      mk("#send", "Отправить заявку", "click", "", "fixed", ""),
    ];
    state.preSteps = [];
    state.activeStep = null;
    renderTable();
    renderSteps();
    saveLocal();
    toast("Демо загружено: нажмите «Запустить»");
  } catch (e) { toast(e.message, "err"); }
}

async function saveNamed() {
  const name = prompt("Название набора настроек:", "Мой сайт");
  if (!name) return;
  try {
    await post("/api/configs", { name, config: collectConfig() });
    toast(`Сохранено: ${name}`);
  } catch (e) { toast(e.message, "err"); }
}

async function openConfigs() {
  el("modalTitle").textContent = "Сохранённые настройки";
  const body = el("modalBody");
  body.innerHTML = "";
  try {
    const { items } = await api("/api/configs");
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Пока ничего не сохранено.";
      body.appendChild(p);
    }
    const list = document.createElement("div");
    list.className = "modal-list";
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "item";
      const name = document.createElement("span");
      name.textContent = it.name;
      const right = document.createElement("div");
      right.style.cssText = "display:flex;gap:6px";
      const open = document.createElement("button");
      open.className = "tiny primary";
      open.textContent = "Загрузить";
      open.onclick = async () => {
        try {
          applyConfig(await api(`/api/configs/${encodeURIComponent(it.name)}`));
          closeModal();
          toast("Настройки загружены");
        } catch (e) { toast(e.message, "err"); }
      };
      const del = document.createElement("button");
      del.className = "tiny ghost";
      del.textContent = "✕";
      del.onclick = async () => {
        await api(`/api/configs/${encodeURIComponent(it.name)}`, { method: "DELETE" });
        row.remove();
      };
      right.append(open, del);
      row.append(name, right);
      list.appendChild(row);
    });
    body.appendChild(list);
    el("modal").classList.remove("hidden");
  } catch (e) { toast(e.message, "err"); }
}

function applyConfig(cfg) {
  el("url").value = cfg.url || "";
  state.steps = (cfg.steps || []).map((s) => ({ ...newStep(), ...s, id: uid() }));
  state.preSteps = (cfg.pre_steps || []).map((s) => ({ ...newStep(true), ...s, id: uid() }));
  el("optHeadless").checked = cfg.headless !== false;
  el("optReload").checked = !!cfg.reload_each_row;
  el("optWait").value = cfg.wait_after_row_ms ?? 500;
  el("optRepeat").value = cfg.repeat_count ?? 1;
  el("optStart").value = (cfg.start_row ?? 0) + 1;
  el("optStopOnError").checked = cfg.stop_on_error !== false;
  el("optShot").checked = cfg.screenshot_on_error !== false;
  renderSteps();
  saveLocal();
}

function closeModal() { el("modal").classList.add("hidden"); }

/* ------------------------------------------------------------ события */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    ["paste", "file", "manual"].forEach((k) => {
      el(`tab-${k}`).classList.toggle("hidden", k !== tab.dataset.tab);
    });
  };
});

el("btnOpenSite").onclick = openSite;
el("btnDemo").onclick = loadDemo;
el("btnPick").onclick = startPick;
el("btnPickDone").onclick = stopPick;
el("btnShot").onclick = takeShot;
el("btnParse").onclick = parsePaste;
el("btnManualParse").onclick = parseManual;
el("file").onchange = (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); };
el("btnAddStep").onclick = () => {
  state.steps.push(newStep());
  state.activeStep = state.steps.length - 1;
  renderSteps();
  saveLocal();
};
el("btnAddPre").onclick = () => { state.preSteps.push(newStep(true)); renderSteps(); saveLocal(); };
el("btnAddPre2").onclick = () => { state.preSteps.push(newStep(true)); renderSteps(); saveLocal(); };
el("btnStart").onclick = startRun;
el("btnStop").onclick = stopRun;
el("btnSave").onclick = saveNamed;
el("btnLoad").onclick = openConfigs;
el("modalClose").onclick = closeModal;
el("modal").onclick = (e) => { if (e.target === el("modal")) closeModal(); };
el("url").oninput = () => saveLocal();
el("thumb").onclick = thumbClick;
el("thumb").addEventListener("wheel", thumbWheel, { passive: false });
["optHeadless", "optReload", "optWait", "optRepeat", "optStart", "optStopOnError", "optShot"].forEach((id) => {
  el(id).onchange = saveLocal;
});

// невидимый чекбокс: показывать окно браузера при выборе полей
const pickWindow = document.createElement("input");
pickWindow.type = "checkbox";
pickWindow.id = "optPickWindow";
pickWindow.checked = true;
pickWindow.style.display = "none";
document.body.appendChild(pickWindow);

loadLocal();
renderSteps();
connectStream();
initStatus();
