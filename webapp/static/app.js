/* Локальный сайт производственных отчётов.
 *
 * Скрипт держит ровно то, что делал чат с ботом: отправить данные, выбрать
 * дату сборки, показать те же текстовые строки и дать ссылки на файлы. Плюс
 * одна вещь, которой в Telegram не было, — сборка не блокирует страницу: задача
 * опрашивается раз в пару секунд, и её можно дождаться, переключившись на другой
 * раздел (а после перезагрузки страница подхватывает идущую сборку из /api/state).
 *
 * Никаких фреймворков и внешних библиотек: сайт должен запускаться на рабочем
 * компьютере цеха без интернета.
 */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  selected: "",          // дата выбранной смены
  timers: {},            // jobId -> интервал опроса задачи
  clocks: {},            // jobId -> интервал «сколько уже идёт»
};

/* ───────────────────────────── запросы ───────────────────────────── */

async function readResponse(res) {
  let data = {};
  try {
    data = await res.json();
  } catch (e) {
    throw new Error(`Ответ сайта не читается (HTTP ${res.status})`);
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

const getJson = (path) => fetch(path).then(readResponse);

const postJson = (path, body) =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then(readResponse);

// Файл идёт ТЕЛОМ запроса: multipart руками не разбираем.
function postRaw(path, params, blob) {
  const query = new URLSearchParams(params);
  return fetch(`${path}?${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: blob,
  }).then(readResponse);
}

/* ───────────────────────────── вывод ───────────────────────────── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function showLines(target, lines) {
  const box = typeof target === "string" ? $(target) : target;
  box.hidden = false;
  box.textContent = "";
  (lines || []).forEach((line) => {
    const parts = String(line).split("\n");
    const node = el("div", "line");
    parts.forEach((part, i) => {
      if (i) node.appendChild(el("br"));
      node.appendChild(document.createTextNode(part));
    });
    box.appendChild(node);
  });
}

function status(target, text, kind) {
  const box = $(target);
  box.hidden = !text;
  box.textContent = text || "";
  box.className = `status ${kind || ""}`.trim();
}

const KINDS = { file: "файл", text: "текст", photo: "фото" };
const kindText = (kind) => KINDS[kind] || kind || "";

function downloadLink(file) {
  const href = `/api/download?date=${encodeURIComponent(file.date || state.selected)}` +
    `&name=${encodeURIComponent(file.name)}`;
  const a = el("a", "file", `⬇ ${file.name}`);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  const meta = el("span", "hint", `${file.kind || ""} · ${file.size_text || ""}`);
  const row = el("div", "filerow");
  row.appendChild(a);
  row.appendChild(meta);
  return row;
}

function showFiles(target, files, date) {
  const box = $(target);
  box.textContent = "";
  box.hidden = !(files && files.length);
  (files || []).forEach((f) => box.appendChild(downloadLink({ ...f, date: f.date || date })));
}

/* ───────────────────────────── приём данных ───────────────────────────── */

async function sendFiles(files) {
  const list = Array.from(files || []);
  if (!list.length) {
    status("intakeErr", "Сначала выберите файл.", "warn");
    return;
  }
  status("intakeErr", "");
  const caption = $("fileCaption").value.trim();
  const lines = [];
  for (const file of list) {
    lines.push(`— ${file.name} (${Math.round(file.size / 1024)} КБ)`);
    try {
      const res = await postRaw("/api/upload_file",
        { name: file.name, caption }, file);
      lines.push(...res.lines);
    } catch (e) {
      lines.push(`⚠️ ${e.message}`);
    }
  }
  $("fileCaption").value = "";
  $("files").value = "";
  showLines("intakeLog", lines);
  refresh();
}

async function sendText() {
  const text = $("text").value.trim();
  if (!text) {
    status("intakeErr", "Сообщение пустое.", "warn");
    return;
  }
  status("intakeErr", "");
  try {
    const res = await postJson("/api/add_text",
      { text, machine: $("machine").value.trim() });
    showLines("intakeLog", res.lines);
    $("text").value = "";
    refresh();
  } catch (e) {
    status("intakeErr", e.message, "warn");
  }
}

/* ───────────────────────────── сборка отчёта ───────────────────────────── */

/* «22.09.2026» -> «20260922», чтобы даты можно было сравнивать строками. */
const dateKey = (d) => {
  const [dd, mm, yyyy] = String(d || "").split(".");
  return `${yyyy || ""}${mm || ""}${dd || ""}`;
};

/* Сколько идёт сборка. Без живых секунд процесс по минутам выглядит мёртвым. */
function spent(iso) {
  const start = Date.parse(iso);
  if (!start) return "";
  const s = Math.max(0, Math.round((Date.now() - start) / 1000));
  if (s < 60) return `${s} с`;
  return `${Math.floor(s / 60)} мин ${String(s % 60).padStart(2, "0")} с`;
}

function trackJob(job, box) {
  // Статус + строки обновляются по ходу; файлы показываем, когда готово.
  const { statusEl, logEl, filesEl, title } = box;
  const stop = () => {
    clearInterval(state.timers[job.id]);
    delete state.timers[job.id];
    clearInterval(state.clocks[job.id]);
    delete state.clocks[job.id];
  };
  let last = job;
  const paint = () => status(statusEl,
    `⏳ ${title}: ${last.progress || "начинаю…"} · идёт ${spent(last.started_at)}`,
    "run");
  if (state.timers[job.id]) clearInterval(state.timers[job.id]);
  if (state.clocks[job.id]) clearInterval(state.clocks[job.id]);
  paint();
  // Секундный «тик» нужен специально: один запрос к ИИ по станку живёт
  // десятки секунд, и без меняющихся секунд страница выглядит зависшей.
  state.clocks[job.id] = setInterval(paint, 1000);
  const tick = async () => {
    let cur;
    try {
      cur = await getJson(`/api/job?id=${encodeURIComponent(job.id)}`);
    } catch (e) {
      stop();
      status(statusEl, `⚠️ ${e.message}`, "warn");
      return;
    }
    last = cur;
    showLines(logEl, cur.lines);
    if (cur.state === "running") {
      paint();
      return;
    }
    stop();
    status(statusEl,
      cur.state === "error" ? "Сборка закончилась ошибкой — см. строки ниже."
        : cur.files && cur.files.length ? "Готово. Файлы ниже."
          : "Готово. Полного отчёта не вышло — см. строки ниже.",
      cur.state === "error" ? "warn" : "ok");
    showFiles(filesEl, cur.files, cur.date);
    refresh();
  };
  state.timers[job.id] = setInterval(tick, 1500);
  tick();
}

/* Сборка за дату: `path` выбирает, платный это путь (/api/build — ИИ спрашивает
 * переписку заново) или бесплатный (/api/rebuild — разбор уже лежит на диске). */
async function runBuild(path, date) {
  const target = date || state.selected;
  if (!target && !date) {
    status("buildStatus", "Выберите дату смены в списке выше.", "warn");
    return;
  }
  status("buildStatus", "");
  try {
    const job = await postJson(path, { date: target });
    trackJob(job, {
      statusEl: "buildStatus", logEl: "buildLog", filesEl: "buildFiles",
      title: `отчёт за ${job.date}`,
    });
  } catch (e) {
    status("buildStatus", `⚠️ ${e.message}`, "warn");
  }
}

function build(date) {
  return runBuild("/api/build", date);
}

function rebuildLocal(date) {
  return runBuild("/api/rebuild", date);
}

async function buildLatest() {
  try {
    const job = await postJson("/api/build", { date: "" });
    trackJob(job, {
      statusEl: "buildStatus", logEl: "buildLog", filesEl: "buildFiles",
      title: `отчёт за ${job.date}`,
    });
  } catch (e) {
    status("buildStatus", `⚠️ ${e.message}`, "warn");
  }
}

async function buildPeriod() {
  const from = $("periodFrom").value;
  const to = $("periodTo").value;
  if (!from || !to) {
    status("periodStatus", "Нет ни одной накопленной смены — сводный " +
                           "отчёт собрать не из чего.", "warn");
    return;
  }
  if (dateKey(from) > dateKey(to)) {
    status("periodStatus", "Дата «с» позже даты «по» — поменяйте их местами.",
           "warn");
    return;
  }
  status("periodStatus", "");
  try {
    const job = await postJson("/api/period", { start: from, end: to });
    trackJob(job, {
      statusEl: "periodStatus", logEl: "periodLog", filesEl: "periodFiles",
      title: "сводный отчёт",
    });
  } catch (e) {
    status("periodStatus", `⚠️ ${e.message}`, "warn");
  }
}

/* Выборы дат периода. Список переставляем только когда он реально изменился,
 * иначе каждые 30 секунд сбрасывался бы выбор человека. */
function fillPeriodDates(dates) {
  const from = $("periodFrom");
  const to = $("periodTo");
  const signature = dates.join(",");
  if (from.dataset.dates === signature) {
    periodInfo(dates, from.value, to.value);
    return;
  }
  from.dataset.dates = signature;
  to.dataset.dates = signature;
  from.textContent = "";
  to.textContent = "";
  dates.forEach((d) => {
    from.appendChild(el("option", null, d));
    to.appendChild(el("option", null, d));
  });
  if (dates.length) {
    from.value = dates[0];
    to.value = dates[dates.length - 1];
  }
  periodInfo(dates, from.value, to.value);
}

function periodInfo(dates, from, to) {
  const box = $("periodInfo");
  if (!dates.length) {
    box.textContent = "Накопленных смен пока нет.";
    return;
  }
  const lo = dateKey(from), hi = dateKey(to);
  const inRange = dates.filter((d) => dateKey(d) >= lo && dateKey(d) <= hi);
  box.textContent = inRange.length
    ? `В периоде ${inRange.length} смен(ы) с данными: `
      + `${inRange.map((d) => d.slice(0, 5)).join(", ")}. Каждая смена — свой `
      + `запрос к ИИ, в среднем 1–2 минуты на смену.`
    : "В выбранном периоде смен с данными нет.";
}

/* ───────────────────────────── архив и журнал ───────────────────────────── */

async function openArchiveDay(date) {
  try {
    const day = await getJson(`/api/archive_day?date=${encodeURIComponent(date)}`);
    showLines("archiveLog", day.lines);
    showFiles("archiveFiles", day.files, date);
    if (day.has_raw) {
      const again = el("button", null, `Пересобрать без ИИ за ${date}`);
      again.onclick = () => rebuildLocal(date);
      $("archiveFiles").appendChild(again);
      $("archiveFiles").hidden = false;
    }
    if (!day.has_files && day.can_build) {
      const btn = el("button", "primary", `Собрать отчёт за ${date}`);
      btn.onclick = () => build(date);
      $("archiveFiles").appendChild(btn);
      $("archiveFiles").hidden = false;
    }
  } catch (e) {
    status("archiveLog", `⚠️ ${e.message}`, "warn");
  }
}

function renderDates(box, items, onClick, selected) {
  const list = $(box);
  list.textContent = "";
  if (!items || !items.length) {
    list.appendChild(el("span", "hint", "пусто — сначала пришлите данные"));
    return;
  }
  items.forEach((item) => {
    const btn = el("button", "date" + (item.date === selected ? " on" : ""), item.line);
    btn.onclick = () => onClick(item.date, btn, list);
    list.appendChild(btn);
  });
}

function renderTable(target, rows, columns) {
  const table = $(target);
  table.textContent = "";
  const head = el("tr");
  columns.forEach((c) => head.appendChild(el("th", null, c.title)));
  table.appendChild(head);
  if (!rows.length) {
    const tr = el("tr");
    const td = el("td", "empty", "записей пока нет");
    td.colSpan = columns.length;
    tr.appendChild(td);
    table.appendChild(tr);
    return;
  }
  rows.forEach((row) => {
    const tr = el("tr");
    columns.forEach((c) => tr.appendChild(el("td", c.cls || null, c.get(row))));
    table.appendChild(tr);
  });
}

/* ───────────────────────────── общая перезагрузка ───────────────────────────── */

async function refresh() {
  let data;
  try {
    data = await getJson("/api/state");
  } catch (e) {
    status("buildStatus", `⚠️ ${e.message}`, "warn");
    return;
  }
  const aiLabel = data.ai_enabled ? (data.ai_model || "включён") : "выключен";
  $("aiBadge").textContent = `ИИ: ${aiLabel}`;
  $("aiBadge").className = `badge ${data.ai_enabled ? "ok" : "warn"}`;
  // Модель видно здесь специально: `.env` читается один раз при запуске, и
  // прежний процесс будет спрашивать прежнюю модель, сколько бы её ни меняли.
  $("aiBadge").title = data.ai_enabled
    ? `формат ${data.ai_format || "?"} — настройки читаются при запуске, `
      + `после правки .env перезапусти программу`
    : "ИИ выключен: отчёт соберётся упрощённо, без расчёта часов по счётчику";
  $("ocrBadge").textContent = `OCR: ${data.ocr_enabled ? "включён" : "выключен"}`;
  $("ocrBadge").className = `badge ${data.ocr_enabled ? "ok" : "warn"}`;
  $("todayBadge").textContent = `Смена: ${data.today}`;
  // Фото на сайте не принимаются, и кнопка «Выбрать файлы» не должна их
  // предлагать: список разрешённых расширений приходит с сервера, поэтому он
  // совпадает с тем, что реально умеет читать приём данных.
  $("files").accept = (data.exts || []).join(",");

  renderDates("shifts", data.shifts, (date, btn, box) => {
    state.selected = date;
    box.querySelectorAll(".date").forEach((b) => b.classList.remove("on"));
    btn.classList.add("on");
  }, state.selected);
  renderDates("archive", data.archive, openArchiveDay);
  fillPeriodDates(data.dates || []);

  const uploads = data.uploads || [];
  renderTable("uploadsTable", uploads, [
    { title: "когда", get: (r) => r.at },
    { title: "что", get: (r) => `${kindText(r.kind)}: ${r.source}` },
    { title: "смена", get: (r) => r.date },
    { title: "станки", get: (r) => (r.machines || []).join(", ") },
    { title: "сообщ.", get: (r) => String(r.messages) },
    { title: "итог", get: (r) => r.error ? `⚠️ ${r.error}` : r.duplicate ? "повтор" : "принято" },
  ]);
  const reports = data.reports || [];
  renderTable("reportsTable", reports, [
    { title: "начало", get: (r) => r.started_at },
    { title: "дата", get: (r) => r.date },
    { title: "вид", get: (r) => (r.scope === "period" ? "сводный" : "смена") },
    { title: "состояние", get: (r) => r.state },
    { title: "файлы", get: (r) => (r.files || []).join(", ") },
    { title: "замечание", get: (r) => r.error || r.note || "" },
  ]);

  // Идущую сборку подхватываем после перезагрузки страницы.
  (data.running || []).forEach((job) => {
    if (state.timers[job.id]) return;
    const period = job.scope === "period";
    trackJob(job, {
      statusEl: period ? "periodStatus" : "buildStatus",
      logEl: period ? "periodLog" : "buildLog",
      filesEl: period ? "periodFiles" : "buildFiles",
      title: period ? "сводный отчёт" : `отчёт за ${job.date}`,
    });
  });
}

/* ───────────────────────────── прочее ───────────────────────────── */

async function resetDay() {
  const date = state.selected;
  if (!date) {
    status("periodStatus", "Сначала выберите дату смены в разделе 2.", "warn");
    return;
  }
  if (!confirm(`Стереть накопленные данные за смену ${date}?\n` +
    "Готовые файлы в архиве останутся.")) return;
  try {
    const res = await postJson("/api/reset", { date });
    showLines("periodLog", res.lines);
    $("periodLog").hidden = false;
    state.selected = "";
    refresh();
  } catch (e) {
    status("periodStatus", `⚠️ ${e.message}`, "warn");
  }
}

async function loadHelp() {
  try {
    const res = await getJson("/api/help");
    $("helpText").textContent = res.text;
  } catch (e) {
    $("helpText").textContent = `Не удалось загрузить справку: ${e.message}`;
  }
}

/* ───────────────────────────── старт ───────────────────────────── */

function bind() {
  // Файл отправляется по кнопке (или сразу при перетаскивании), а не в момент
  // выбора: так можно приложить подпись к первому файлу и отправить несколько
  // штук одной кнопкой.
  $("sendFiles").onclick = () => sendFiles($("files").files);
  $("sendText").onclick = sendText;

  const drop = $("drop");
  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add("hot"); }));
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove("hot"); }));
  drop.addEventListener("drop", (e) => {
    // Картинки на приём больше не выставляем: данные смены приходят файлом
    // переписки или текстом. Притащили фото — говорим об этом прямо, а не
    // молча выбрасываем.
    const all = Array.from(e.dataTransfer.files || []);
    const pics = all.filter((f) => (f.type || "").startsWith("image/"));
    const docs = all.filter((f) => !pics.includes(f));
    if (docs.length) sendFiles(docs);
    if (pics.length && !docs.length) {
      status("intakeErr", "Фото на сайте не принимается — пришлите переписку " +
                          "файлом или текстом.", "warn");
    }
  });

  ["periodFrom", "periodTo"].forEach((id) => $(id).addEventListener("change", () => {
    const dates = ($("periodFrom").dataset.dates || "").split(",").filter(Boolean);
    periodInfo(dates, $("periodFrom").value, $("periodTo").value);
  }));

  $("buildBtn").onclick = () => build();
  $("rebuildBtn").onclick = () => rebuildLocal();
  $("buildLatest").onclick = buildLatest;
  $("refreshShifts").onclick = refresh;
  $("periodBtn").onclick = buildPeriod;
  $("resetBtn").onclick = resetDay;

  $("text").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") sendText();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bind();
  loadHelp();
  refresh();
  // Списки смен и журнал обновляются сами: данные мог прислать другой человек.
  setInterval(refresh, 30000);
});
