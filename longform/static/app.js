/* longform app: everything the window shows. Talks to the local server in server.py. */
"use strict";

// ---------------------------------------------------------------- utilities
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function fmt(sec) {
  if (sec == null || !isFinite(sec)) return "–";
  sec = Math.max(0, Math.floor(sec + 1e-6));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
const secs = (v) => `${(Math.round(v * 10) / 10).toFixed(1)}s`;

const ICONS = {
  plus: "M12 5v14M5 12h14", play: "M7 4.5v15l12.5-7.5z", pause: "M8 5v14M16 5v14",
  undo: "M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3", redo: "M15 14l5-5-5-5M20 9H9a5 5 0 000 10h3",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3", copy: "M8 8h12v12H8zM4 16V4h12",
  film: "M3 5h18v14H3zM7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4",
  music: "M9 18V5l11-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zM20 16a3 3 0 11-6 0 3 3 0 016 0z",
  text: "M4 7V4h16v3M12 4v16M9 20h6", image: "M3 5h18v14H3zM3 16l5-5 5 5 3-3 5 5M15.5 9.5h.01",
  sparkles: "M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z",
  cc: "M3 5h18v14H3zM10.5 10H9a2 2 0 000 4h1.5M17 10h-1.5a2 2 0 000 4H17",
  export: "M12 15V3M7 8l5-5 5 5M4 15v5h16v-5", folder: "M3 6h6l2 2h10v11H3z",
  check: "M5 12l5 5L20 7", x: "M6 6l12 12M18 6L6 18", down: "M6 9l6 6 6-6",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
  volume: "M4 9h4l5-4v14l-5-4H4zM17 9a4 4 0 010 6", mute: "M4 9h4l5-4v14l-5-4H4zM16 9l5 6M21 9l-5 6",
  bookmark: "M6 3h12v18l-6-4-6 4z", swap: "M4 12h16M8 8l-4 4 4 4M16 8l4 4-4 4",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", refresh: "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16", info: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-4M12 8h.01",
  zoom: "M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-4.3-4.3", more: "M5 12h.01M12 12h.01M19 12h.01",
  left: "M15 6l-6 6 6 6", right: "M9 6l6 6-6 6", fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
};
const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24"><path d="${ICONS[name] || ""}"/></svg>`;

const STYLE_NAMES = {
  fade: "Crossfade", dissolve: "Dissolve", fadeblack: "Dip to black", fadewhite: "Dip to white",
  wipeleft: "Wipe left", wiperight: "Wipe right", wipeup: "Wipe up", wipedown: "Wipe down",
  slideleft: "Slide left", slideright: "Slide right", slideup: "Slide up", slidedown: "Slide down",
  smoothleft: "Smooth left", smoothright: "Smooth right", smoothup: "Smooth up", smoothdown: "Smooth down",
  circleopen: "Circle open", circleclose: "Circle close", circlecrop: "Circle crop", rectcrop: "Box crop",
  radial: "Radial", pixelize: "Pixelize", distance: "Distance", hblur: "Blur", zoomin: "Zoom in",
};
const MODEL_NAMES = { tiny: "Fast", base: "Balanced", small: "Accurate" };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const res = await fetch("/api/" + path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(message, { error = false, action = null, timeout = 4500 } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.innerHTML = `<span>${esc(message)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.onclick = () => { action.run(); el.remove(); };
    el.append(b);
  }
  $("toasts").append(el);
  setTimeout(() => el.remove(), error ? timeout * 2 : timeout);
}
const fail = (err) => toast(err.message || String(err), { error: true });

// ---------------------------------------------------------------- state
const S = {
  catalog: null, desktop: false, settings: {},
  library: [], byPath: new Map(), byId: new Map(),
  projects: [], pid: null, project: null,
  history: [], future: [], lastCommitKey: null, lastCommitAt: 0,
  selected: null, tab: "media", zoom: 40, autoFit: true,
  jobs: new Map(), player: { mode: "auto", jobId: null }, previewSig: null, previewJob: null,
  exportJob: null, exportName: "", saveTimer: null, saving: false, uploads: [],
  proxies: new Map(), proxyWaiters: new Map(),
};

// The window's built-in player can't decode every format (HEVC, ProRes, AVI...). When a clip
// fails to play, ask the server for a small H.264 copy and switch to it once it's ready.
const mediaSrc = (m) => (S.proxies.get(m.id) ? `/api/proxy/${m.id}` : `/api/media/${m.id}`);
function playWithFallback(video, m, seek = 0) {
  video.addEventListener("error", async () => {
    if (S.proxies.get(m.id)) return;
    const waiters = S.proxyWaiters.get(m.id) || [];
    waiters.push(() => { video.src = `/api/proxy/${m.id}#t=${seek}`; });
    S.proxyWaiters.set(m.id, waiters);
    if (waiters.length > 1) return;
    try {
      const job = await api(`proxy/${m.id}`, { method: "POST" });
      S.jobs.set(job.id, job);
      if (job.status === "done") return jobFinished(job);
      renderPlayer(); pollJobs();
    } catch (err) { fail(err); }
  }, { once: true });
}

function newClip(media) {
  const type = media?.is_image ? "image" : media ? "video" : "title";
  return {
    type, path: media ? media.path : null, start: 0, end: null, duration: type === "image" ? 5 : type === "title" ? 4 : null,
    text: type === "title" ? "Your title" : null, font_size: null, color: "#ffffff", background: "#000000",
    fade_in: 0, fade_out: 0, volume: 1, mute: false, chapter: null, transition: null, transition_style: null, captions: true,
  };
}

// ---------------------------------------------------------------- timing (mirrors render.py)
function mediaFor(clip) { return clip.path ? S.byPath.get(clip.path) : null; }
function clipDuration(c) {
  if (c.type === "title") return c.duration || 4;
  const m = mediaFor(c);
  if (c.type === "image" || m?.is_image) return c.duration || 5;
  if (!m || m.duration == null) return c.duration || 5;
  const end = c.end != null ? Math.min(c.end, m.duration) : m.duration;
  let d = Math.max(0.1, end - (c.start || 0));
  if (c.duration) d = Math.min(d, c.duration);
  return d;
}
function layout(p = S.project) {
  const durs = p.clips.map(clipDuration);
  const overlaps = durs.map((d, i) => {
    if (i === 0) return 0;
    const c = p.clips[i];
    const want = c.transition ?? p.transition ?? 0;
    return Math.max(0, Math.min(want, durs[i - 1] / 2, d / 2));
  });
  const starts = [];
  durs.forEach((d, i) => starts.push(i === 0 ? 0 : starts[i - 1] + durs[i - 1] - overlaps[i]));
  const total = durs.length ? starts.at(-1) + durs.at(-1) : 0;
  return { durs, overlaps, starts, total };
}
// Where each clip is drawn: overlaps are split at their midpoint so clips sit edge to edge
// and both trim handles stay reachable; the transition badge sits on the seam.
function geometry(L = layout()) {
  return L.durs.map((d, i) => ({
    a: L.starts[i] + L.overlaps[i] / 2,
    b: L.starts[i] + d - (L.overlaps[i + 1] || 0) / 2,
  }));
}
function dropIndexAt(t) {
  const g = geometry();
  const i = g.findIndex((x) => t < (x.a + x.b) / 2);
  return i < 0 ? g.length : i;
}
function seamX(to) {
  const g = geometry();
  return (to < g.length ? g[to].a : g.at(-1)?.b ?? 0) * S.zoom;
}
function clipName(c) {
  if (c.type === "title") return (c.text || "Title").split("\n")[0] || "Title";
  return (c.path || "").split(/[\\/]/).pop();
}
function hasSpeech(c) {
  const m = mediaFor(c);
  return c.type === "video" && m && !m.is_image && m.has_audio && !c.mute;
}

// ---------------------------------------------------------------- history and saving
function commit(change, key = null) {
  const now = Date.now();
  if (!(key && key === S.lastCommitKey && now - S.lastCommitAt < 1200)) {
    S.history.push(JSON.stringify(S.project));
    if (S.history.length > 200) S.history.shift();
    S.future = [];
  }
  S.lastCommitKey = key; S.lastCommitAt = now;
  change(S.project);
  afterChange();
}
function afterChange({ panels = false } = {}) {
  if (S.selected != null && S.selected >= S.project.clips.length) S.selected = S.project.clips.length ? S.project.clips.length - 1 : null;
  scheduleSave();
  renderTimeline(); renderTopbar(); renderPlayerStatus();
  if (panels) { renderPanel(); renderInspector(); }
  else if (S.selected == null) renderInspector();  // the project summary
  // Title cards and photos are drawn by the page itself, so redraw them as they change.
  if (S.player.mode === "auto" && !activeVideo()) renderPlayer();
}
function undo() {
  if (!S.history.length) return;
  S.future.push(JSON.stringify(S.project));
  S.project = JSON.parse(S.history.pop());
  S.lastCommitKey = null;
  afterChange({ panels: true });
}
function redo() {
  if (!S.future.length) return;
  S.history.push(JSON.stringify(S.project));
  S.project = JSON.parse(S.future.pop());
  S.lastCommitKey = null;
  afterChange({ panels: true });
}
function scheduleSave() {
  $("saveState").textContent = "Saving…";
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveNow, 600);
}
async function saveNow() {
  clearTimeout(S.saveTimer);
  if (!S.pid) return;
  try {
    await api(`projects/${S.pid}`, { method: "PUT", body: S.project });
    $("saveState").textContent = "All changes saved";
  } catch (err) {
    $("saveState").textContent = "Not saved";
    fail(err);
  }
}

// ---------------------------------------------------------------- loading
async function loadLibrary() {
  S.library = await api("library");
  S.byPath = new Map(S.library.map((m) => [m.path, m]));
  S.byId = new Map(S.library.map((m) => [m.id, m]));
}
async function openProject(pid) {
  if (S.pid) await saveNow();
  const project = await api(`projects/${pid}`);
  S.pid = pid; S.project = project; delete S.project.id;
  S.history = []; S.future = []; S.selected = null; S.autoFit = true;
  S.player = { mode: "auto", jobId: null }; S.previewSig = null; S.previewJob = null; S.exportJob = null;
  S.exportName = project.name || "My video";
  try { localStorage.setItem("longform.project", pid); } catch {}
  renderAll();
}
async function refreshProjects() { S.projects = await api("projects"); }

async function init() {
  const state = await api("state");
  S.catalog = state.catalog; S.desktop = state.desktop; S.settings = state.settings; S.projects = state.projects;
  for (const j of state.jobs) S.jobs.set(j.id, j);
  await loadLibrary();
  let pid = null;
  try { pid = localStorage.getItem("longform.project"); } catch {}
  if (!S.projects.some((p) => p.id === pid)) pid = S.projects[0]?.id;
  if (!pid) { pid = (await api("projects", { method: "POST", body: { name: "My first video" } })).id; await refreshProjects(); }
  await openProject(pid);
  setupStatic();
  pollJobs();
}

// ---------------------------------------------------------------- top bar
function setupStatic() {
  $("projectsBtn").innerHTML = icon("grid") + icon("down", "sm");
  $("undoBtn").innerHTML = icon("undo"); $("redoBtn").innerHTML = icon("redo");
  $("previewBtn").innerHTML = icon("eye") + "Preview";
  $("exportBtn").innerHTML = icon("export") + "Export";
  $("undoBtn").onclick = undo; $("redoBtn").onclick = redo;
  $("previewBtn").onclick = startPreview;
  $("exportBtn").onclick = () => { S.tab = "export"; renderTabs(); renderPanel(); };
  $("projectsBtn").onclick = (e) => { e.stopPropagation(); toggleProjectsMenu(); };
  $("projectName").addEventListener("input", (e) => commit((p) => { p.name = e.target.value; }, "name"));
  $("projectName").addEventListener("change", refreshProjects);
  setupTimelineEvents(); setupKeyboard(); setupFileDrop();
  $("fileInput").onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ""; };
  document.addEventListener("click", (e) => { if (!e.target.closest("#projectsMenu")) $("projectsMenu").hidden = true; });
  $("modal").addEventListener("mousedown", (e) => { if (e.target === $("modal")) closeModal(); });
}
function renderTopbar() {
  const nameInput = $("projectName");
  if (document.activeElement !== nameInput) nameInput.value = S.project.name || "";
  $("undoBtn").disabled = !S.history.length; $("redoBtn").disabled = !S.future.length;
  const has = S.project.clips.length > 0;
  $("previewBtn").disabled = !has; $("exportBtn").disabled = !has;
  const running = [...S.jobs.values()].filter((j) => j.status === "running" && (j.kind === "export" || j.kind === "captions"));
  $("topJobs").innerHTML = running.map((j) => `<div class="mini-progress" title="${esc(j.message)}">
      <span>${esc(j.kind === "export" ? "Exporting" : "Captions")}</span>
      <div class="bar"><div style="width:${j.progress * 100}%"></div></div><span>${Math.round(j.progress * 100)}%</span></div>`).join("");
}

function renderAll() {
  renderTopbar(); renderTabs(); renderPanel(); renderInspector(); renderPlayer();
  if (S.autoFit) fitTimeline(); else renderTimeline();
  $("saveState").textContent = "All changes saved";
}

// ---------------------------------------------------------------- projects menu
function toggleProjectsMenu() {
  const menu = $("projectsMenu");
  if (!menu.hidden) { menu.hidden = true; return; }
  const r = $("projectsBtn").getBoundingClientRect();
  menu.style.left = r.left + "px"; menu.style.top = r.bottom + 6 + "px";
  menu.innerHTML = `
    <button class="item" data-act="new">${icon("plus")}<div class="grow"><b>New project</b></div></button>
    <div class="sep"></div>
    ${S.projects.map((p) => `<button class="item ${p.id === S.pid ? "current" : ""}" data-open="${p.id}">
        <div class="mini-thumb" style="${p.cover ? `background-image:url('/api/thumb/${p.cover}')` : ""}"></div>
        <div class="grow"><div>${esc(p.name)}</div><div class="sub">${p.clips} clip${p.clips === 1 ? "" : "s"} · ${new Date(p.updated * 1000).toLocaleDateString()}</div></div>
      </button>`).join("")}
    <div class="sep"></div>
    <button class="item" data-act="dup">${icon("copy")}<div class="grow">Duplicate this project</div></button>
    <button class="item" data-act="del">${icon("trash")}<div class="grow">Delete this project</div></button>`;
  menu.hidden = false;
  menu.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    menu.hidden = true;
    try {
      if (b.dataset.open) return openProject(b.dataset.open);
      if (b.dataset.act === "new") return newProjectModal();
      await saveNow();
      if (b.dataset.act === "dup") {
        const p = await api(`projects/${S.pid}/duplicate`, { method: "POST" });
        await refreshProjects(); await openProject(p.id); toast("Project duplicated");
      }
      if (b.dataset.act === "del") {
        if (!(await confirmModal(`Delete “${S.project.name}”?`, "Your video files are not touched. Only this project's timeline is removed.", "Delete project"))) return;
        await api(`projects/${S.pid}`, { method: "DELETE" });
        S.pid = null; await refreshProjects();
        if (!S.projects.length) await api("projects", { method: "POST", body: { name: "My first video" } }), await refreshProjects();
        await openProject(S.projects[0].id);
      }
    } catch (err) { fail(err); }
  };
}
function newProjectModal() {
  let chosen = null;
  openModal(`<header><h2>New project</h2></header>
    <div class="body">
      <label class="field"><span>Name</span><input type="text" id="npName" value="New video"></label>
      <div class="section-title">Start from a look (optional)</div>
      <div class="choice-grid" id="npTemplates">${S.catalog.templates.map((t) => `<button class="choice" data-id="${t.id}">
          <span class="t">${t.icon} ${esc(t.name)}</span><span class="d">${esc(t.description)}</span></button>`).join("")}</div>
    </div>
    <footer><button id="npCancel">Cancel</button><button class="primary" id="npCreate">Create project</button></footer>`);
  $("npName").select();
  $("npTemplates").onclick = (e) => {
    const b = e.target.closest(".choice"); if (!b) return;
    chosen = chosen === b.dataset.id ? null : b.dataset.id;
    [...$("npTemplates").children].forEach((c) => c.classList.toggle("selected", c.dataset.id === chosen));
  };
  $("npCancel").onclick = closeModal;
  $("npCreate").onclick = async () => {
    try {
      const p = await api("projects", { method: "POST", body: { name: $("npName").value, template_id: chosen } });
      closeModal(); await refreshProjects(); await openProject(p.id);
    } catch (err) { fail(err); }
  };
}

// ---------------------------------------------------------------- modals
function openModal(html) { $("modalBody").innerHTML = html; $("modal").hidden = false; }
function closeModal() { $("modal").hidden = true; $("modalBody").innerHTML = ""; }
function confirmModal(title, text, okLabel = "OK") {
  return new Promise((resolve) => {
    openModal(`<header><h2>${esc(title)}</h2></header><div class="body"><p class="hint">${esc(text)}</p></div>
      <footer><button id="cfNo">Cancel</button><button class="primary" id="cfYes">${esc(okLabel)}</button></footer>`);
    $("cfNo").onclick = () => { closeModal(); resolve(false); };
    $("cfYes").onclick = () => { closeModal(); resolve(true); };
  });
}

// ---------------------------------------------------------------- sidebar
const TABS = [
  { id: "media", label: "Media", icon: "film" }, { id: "style", label: "Style", icon: "sparkles" },
  { id: "captions", label: "Captions", icon: "cc" }, { id: "export", label: "Export", icon: "export" },
];
function renderTabs() {
  $("tabs").innerHTML = TABS.map((t, i) => `<button class="tab ${S.tab === t.id ? "active" : ""}" data-tab="${t.id}">
      ${icon(t.icon)}<span>${t.label}</span><span class="step">STEP ${i + 1}</span></button>`).join("");
  $("tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { S.tab = b.dataset.tab; renderTabs(); renderPanel(); } };
}
function renderPanel() {
  const panel = $("panel");
  const scroll = panel.scrollTop;
  panel.innerHTML = { media: mediaPanel, style: stylePanel, captions: captionsPanel, export: exportPanel }[S.tab]();
  panel.scrollTop = scroll;
}

// Generic bindings: <input data-bind="p.output.loudnorm" data-kind="bool"> or "c.fade_in" for the selected clip.
function bindTarget(path) {
  const parts = path.split(".");
  let obj = parts[0] === "c" ? S.project.clips[S.selected] : S.project;
  for (const k of parts.slice(1, -1)) obj = obj[k] ??= {};
  return [obj, parts.at(-1)];
}
function readBound(el) {
  const kind = el.dataset.kind || "text";
  if (kind === "bool") return el.checked;
  if (kind === "number") return el.value === "" ? 0 : Number(el.value);
  if (kind === "maybe-number") return el.value === "" ? null : Number(el.value);
  if (kind === "maybe-text") return el.value.trim() === "" ? null : el.value;
  return el.value;
}
function onBoundInput(e) {
  const el = e.target.closest("[data-bind]"); if (!el) return;
  if (el.dataset.bind.startsWith("c.") && S.selected == null) return;
  const value = readBound(el);
  commit(() => { const [obj, key] = bindTarget(el.dataset.bind); obj[key] = value; }, el.dataset.bind);
  if (el.dataset.rerender) { renderPanel(); renderInspector(); }
  const out = el.parentElement.querySelector(".value"); if (out) out.textContent = el.dataset.fmt === "pct" ? `${Math.round(value * 100)}%` : secs(value);
}
for (const id of ["panel", "inspector"]) {
  document.addEventListener("DOMContentLoaded", () => {
    $(id).addEventListener("input", (e) => { if (e.target.type !== "checkbox" && e.target.tagName !== "SELECT") onBoundInput(e); });
    $(id).addEventListener("change", (e) => { if (e.target.type === "checkbox" || e.target.tagName === "SELECT") onBoundInput(e); });
  });
}
function sw(bind, label, sub, checked, rerender = true) {
  return `<label class="switch"><span class="label"><b>${label}</b>${sub ? `<small>${sub}</small>` : ""}</span>
    <input type="checkbox" data-bind="${bind}" data-kind="bool" ${rerender ? 'data-rerender="1"' : ""} ${checked ? "checked" : ""}><span class="knob"></span></label>`;
}
function segmented(name, options, current) {
  return `<div class="segmented" data-seg="${name}">${options.map(([v, label]) =>
    `<button data-v="${esc(JSON.stringify(v))}" class="${JSON.stringify(v) === JSON.stringify(current) ? "on" : ""}">${label}</button>`).join("")}</div>`;
}
function slider(bind, value, min, max, step, fmtKind = "s") {
  const shown = fmtKind === "pct" ? `${Math.round(value * 100)}%` : secs(value);
  return `<div class="row"><input type="range" data-bind="${bind}" data-kind="number" data-fmt="${fmtKind}" min="${min}" max="${max}" step="${step}" value="${value}"><span class="value hint" style="flex:0 0 46px;text-align:right">${shown}</span></div>`;
}
// Segmented buttons call this with the control's name and chosen value.
const SEGMENTS = {
  "transition-len": (v) => commit((p) => { p.transition = v; }),
  "chapters": (v) => commit((p) => { p.chapters = v; }),
  "cap-size": (v) => commit((p) => { p.captions.size = v; }),
  "cap-model": (v) => commit((p) => { p.captions.model = v; }),
  "quality": (v) => commit((p) => { p.output.crf = v; }),
  "fit": (v) => commit((p) => { p.output.fit = v; }),
  "clip-transition": (v) => commit((p) => { p.clips[S.selected].transition = v; }),
  "title-size": (v) => commit((p) => { p.clips[S.selected].font_size = v; }),
};
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-seg] button"); if (!b) return;
  SEGMENTS[b.parentElement.dataset.seg]?.(JSON.parse(b.dataset.v));
  renderPanel(); renderInspector();
});

// ----- media tab
function mediaCard(m) {
  const kindPill = m.is_audio ? `<span class="pill">${icon("music", "sm")}Audio</span>` : m.is_image ? `<span class="pill">${icon("image", "sm")}Photo</span>` : "";
  const capsPill = m.has_captions ? `<span class="pill ok">${icon("cc", "sm")}</span>` : "";
  const sub = m.missing ? "File moved or deleted" : m.error ? "Can't read this file" :
    [m.width ? `${m.width}×${m.height}` : "", !m.is_image && m.has_video && !m.has_audio ? "no sound" : ""].filter(Boolean).join(" · ");
  return `<div class="card ${m.missing || m.error ? "missing" : ""}" draggable="${!m.error}" data-media="${m.id}" title="${esc(m.path)}">
    <div class="thumb" style="${m.error ? "" : `background-image:url('/api/thumb/${m.id}')`}">
      <div class="kind">${kindPill}${capsPill}</div>
      ${!m.is_image && m.duration ? `<span class="dur">${fmt(m.duration)}</span>` : ""}
      ${m.error ? "" : m.is_audio ? `<button class="add" data-music="${m.id}">${icon("music", "sm")}Use</button>` : `<button class="add" data-add="${m.id}">${icon("plus", "sm")}Add</button>`}
      <button class="more icon-btn" data-remove="${m.id}" title="Remove from library">${icon("trash", "sm")}</button>
    </div>
    <div class="meta"><div class="name">${esc(m.name)}</div><div class="sub">${esc(sub) || "&nbsp;"}</div></div></div>`;
}
function mediaPanel() {
  const visual = S.library.filter((m) => !m.is_audio), audio = S.library.filter((m) => m.is_audio);
  const uploads = S.uploads.map((u) => `<div class="list-item"><div class="grow"><div class="name">${esc(u.name)}</div>
      <div class="bar"><div style="width:${u.progress * 100}%"></div></div></div></div>`).join("");
  return `
    <div class="dropzone" id="addMedia">${icon("plus", "lg")}<b>Add videos, photos & music</b>
      <span class="hint">${S.desktop ? "Click to choose files, or drag them here" : "Click to choose, or drop files anywhere"}</span></div>
    ${uploads ? `<div class="list">${uploads}</div>` : ""}
    <div class="row">
      <button id="addAll" ${visual.some((m) => !m.error) ? "" : "disabled"}>${icon("film", "sm")}Add all to timeline</button>
      <button id="addTitle">${icon("text", "sm")}Title card</button>
    </div>
    <div class="section">
      <div class="section-title">Videos & photos <span class="grow"></span><span>${visual.length}</span></div>
      ${visual.length ? `<div class="library">${visual.map(mediaCard).join("")}</div>`
        : `<p class="hint">Nothing here yet. Add the clips you want to put together. They stay where they are on your computer.</p>`}
    </div>
    <div class="section">
      <div class="section-title">Music & audio <span class="grow"></span><span>${audio.length}</span></div>
      ${audio.length ? `<div class="library">${audio.map(mediaCard).join("")}</div>`
        : `<p class="hint">Add a song to play under your video. Choose it in <b>Style</b>.</p>`}
    </div>`;
}
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#panel")) return;
  const t = e.target;
  try {
    if (t.closest("#addMedia")) return pickFiles();
    if (t.closest("#addAll")) {
      const items = S.library.filter((m) => !m.is_audio && !m.error && !m.missing);
      return commit((p) => { p.clips.push(...items.map(newClip)); }), fitTimeline(), toast(`Added ${items.length} clips`);
    }
    if (t.closest("#addTitle")) return addClipAt(newClip(null));
    const add = t.closest("[data-add]"); if (add) return addClipAt(newClip(S.byId.get(add.dataset.add)));
    const music = t.closest("[data-music]");
    if (music) {
      const m = S.byId.get(music.dataset.music);
      commit((p) => { p.music = { path: m.path, volume: p.music?.volume ?? 0.25, duck: p.music?.duck ?? true, fade_out: 3 }; });
      return toast(`“${m.name}” will play under your video`, { action: { label: "Music settings", run: () => { S.tab = "style"; renderTabs(); renderPanel(); } } });
    }
    const rm = t.closest("[data-remove]");
    if (rm) {
      const m = S.byId.get(rm.dataset.remove);
      const used = S.project.clips.some((c) => c.path === m.path) || S.project.music?.path === m.path;
      if (used && !(await confirmModal(`Remove ${m.name}?`, "It's used in this project. It will show as missing until you add it again. The file itself is not deleted.", "Remove"))) return;
      await api(`library/${m.id}`, { method: "DELETE" });
      await loadLibrary(); renderPanel(); renderTimeline();
    }
  } catch (err) { fail(err); }
});
document.addEventListener("dragstart", (e) => {
  const card = e.target.closest?.("[data-media]"); if (!card) return;
  e.dataTransfer.setData("application/x-longform-media", card.dataset.media);
  e.dataTransfer.effectAllowed = "copy";
});
function addClipAt(clip, index = null) {
  const at = index ?? (S.selected != null ? S.selected + 1 : S.project.clips.length);
  commit((p) => { p.clips.splice(at, 0, clip); });
  S.selected = at; S.player.mode = "auto";
  renderInspector(); renderPlayer(); renderTimeline();
  if (S.autoFit) fitTimeline();
}

async function pickFiles() {
  if (S.desktop && window.pywebview?.api?.pick_files) {
    const paths = await window.pywebview.api.pick_files();
    if (paths?.length) await addPaths(paths);
  } else {
    $("fileInput").click();
  }
}
async function addPaths(paths) {
  try {
    const added = await api("library", { method: "POST", body: { paths } });
    await loadLibrary(); renderPanel(); renderTimeline();
    const visual = added.filter((m) => !m.is_audio && !m.error);
    if (visual.length && !S.project.clips.length) {
      commit((p) => { p.clips.push(...visual.map(newClip)); }); fitTimeline();
      toast(`Added ${visual.length} clip${visual.length === 1 ? "" : "s"} to your timeline`);
    } else {
      toast(`Added ${added.length} file${added.length === 1 ? "" : "s"} to your library`);
    }
  } catch (err) { fail(err); }
}
window.app = { addPaths };

function uploadFiles(files) {
  const queue = files.map((f) => ({ file: f, name: f.name, progress: 0 }));
  S.uploads.push(...queue);
  if (S.tab !== "media") { S.tab = "media"; renderTabs(); }
  renderPanel();
  const added = [];
  const next = () => {
    const u = queue.shift();
    if (!u) {
      loadLibrary().then(() => {
        const visual = added.filter((m) => !m.is_audio && !m.error);
        if (visual.length && !S.project.clips.length) { commit((p) => { p.clips.push(...visual.map(newClip)); }); fitTimeline(); }
        renderPanel(); renderTimeline();
      });
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", "/api/upload/" + encodeURIComponent(u.name));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { u.progress = e.loaded / e.total; renderPanel(); } };
    xhr.onloadend = () => {
      S.uploads.splice(S.uploads.indexOf(u), 1);
      const data = JSON.parse(xhr.responseText || "{}");
      if (xhr.status >= 300) toast(`${u.name}: ${data.error || "upload failed"}`, { error: true }); else added.push(data);
      next();
    };
    xhr.send(u.file);
  };
  next();
}
function setupFileDrop() {
  let depth = 0;
  const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (isFiles(e)) { depth++; $("dropOverlay").hidden = false; } });
  window.addEventListener("dragleave", (e) => { if (isFiles(e) && --depth <= 0) { depth = 0; $("dropOverlay").hidden = true; } });
  window.addEventListener("dragover", (e) => { if (isFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!isFiles(e)) return;
    e.preventDefault(); depth = 0; $("dropOverlay").hidden = true;
    // In the desktop app the window hands dropped files' real paths to Python (see desktop.py).
    if (S.desktop && window.pywebview) return;
    uploadFiles([...e.dataTransfer.files]);
  });
}

// ----- style tab
function stylePanel() {
  const p = S.project, c = S.catalog;
  const audio = S.library.filter((m) => (m.is_audio || (m.has_audio && !m.has_video)) && !m.error);
  const lengths = [[0, "Cut"], [0.25, "Quick"], [0.5, "Smooth"], [1, "Slow"], [2, "Long"]];
  return `
    <div class="section">
      <div class="section-title">${icon("sparkles", "sm")} One-click looks</div>
      <p class="hint">Pick a look to set transitions, captions, sound and chapters in one go. You can still change anything after.</p>
      <div class="choice-grid">${c.templates.map((t) => `<button class="choice ${p.template === t.id ? "selected" : ""}" data-template="${t.id}">
        <span class="t">${t.icon} ${esc(t.name)}</span><span class="d">${esc(t.description)}</span></button>`).join("")}</div>
    </div>
    <div class="section">
      <div class="section-title">${icon("swap", "sm")} Transitions between clips</div>
      ${segmented("transition-len", lengths, lengths.some(([v]) => v === p.transition) ? p.transition : null)}
      <label class="field" ${p.transition ? "" : "hidden"}><span>Style</span>
        <select data-bind="p.transition_style">${c.transition_styles.map((s) => `<option value="${s}" ${s === p.transition_style ? "selected" : ""}>${STYLE_NAMES[s] || s}</option>`).join("")}</select></label>
      <p class="hint">Set a different transition for one clip by selecting it on the timeline.</p>
    </div>
    <div class="section">
      <div class="section-title">${icon("music", "sm")} Background music</div>
      <select id="musicSelect">
        <option value="">No music</option>
        ${audio.map((m) => `<option value="${esc(m.path)}" ${p.music?.path === m.path ? "selected" : ""}>${esc(m.name)}</option>`).join("")}
      </select>
      ${p.music ? `<label class="field"><span>Music volume</span>${slider("p.music.volume", p.music.volume, 0, 1, 0.05, "pct")}</label>
        ${sw("p.music.duck", "Lower music when people talk", "Keeps voices clear", p.music.duck, false)}`
        : `<button id="addMusic">${icon("plus", "sm")}Add a music file</button>`}
    </div>
    <div class="section">
      <div class="section-title">${icon("volume", "sm")} Sound</div>
      ${sw("p.output.loudnorm", "Even out the volume", "Makes quiet and loud clips sound the same", p.output.loudnorm, false)}
    </div>
    <div class="section">
      <div class="section-title">${icon("bookmark", "sm")} Chapters</div>
      ${segmented("chapters", [["none", "Off"], ["marked", "Named only"], ["clips", "Every clip"]], p.chapters)}
      <p class="hint">Chapters show up as sections on YouTube. Name a clip's chapter by selecting it.</p>
    </div>`;
}
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-template]");
  if (t && e.target.closest("#panel")) {
    const preset = S.catalog.templates.find((x) => x.id === t.dataset.template);
    commit((p) => { Object.assign(p, applyPreset(p, preset)); });
    renderPanel(); renderInspector();
    toast(`Applied the ${preset.name} look`, { action: { label: "Undo", run: undo } });
  }
  if (e.target.closest("#addMusic")) pickFiles();
});
// The music <select> holds a path; turn it into a music object (or remove it).
document.addEventListener("change", (e) => {
  if (e.target.id !== "musicSelect") return;
  const path = e.target.value;
  commit((p) => { p.music = path ? { path, volume: p.music?.volume ?? 0.25, duck: p.music?.duck ?? true, fade_out: 3 } : null; });
  renderPanel(); renderInspector();
}, true);
function applyPreset(project, preset) {
  const r = clone(project);
  for (const [key, value] of Object.entries(preset.changes)) {
    if (key === "music") { if (r.music) r.music = { ...r.music, ...value }; }
    else if (key === "edge_fade") { if (r.clips.length) { r.clips[0].fade_in = value; r.clips.at(-1).fade_out = value; } }
    else if (key === "image_duration") r.clips.forEach((c) => { if (c.type === "image") c.duration = value; });
    else if (value && typeof value === "object") r[key] = { ...(r[key] || {}), ...value };
    else r[key] = value;
  }
  if (S.catalog.templates.includes(preset)) r.template = preset.id; else r.export_preset = preset.id;
  return r;
}

// ----- captions tab
function speechSources() {
  const seen = new Map();
  for (const c of S.project.clips) {
    const m = mediaFor(c);
    if (hasSpeech(c) && c.captions !== false && !seen.has(m.id)) seen.set(m.id, m);
  }
  return [...seen.values()];
}
function captionJob(mid) { return [...S.jobs.values()].find((j) => j.kind === "captions" && j.result?.key === mid && j.status === "running"); }
function captionsPanel() {
  const p = S.project, cap = p.captions;
  if (!S.catalog.captions_available) {
    return `<div class="notice warn">${icon("info")}<div><b>The captions engine isn't installed.</b><br>
      The desktop app includes it. If you run longform from Python, install it with <b>pip install faster-whisper</b> and restart.</div></div>`;
  }
  const sources = speechSources();
  const sample = { clean: "Welcome back to the channel", bold: "WELCOME BACK", boxed: "Welcome back to the channel" };
  const styleCss = {
    clean: "color:#fff;font-weight:700;text-shadow:0 0 3px #000,0 0 3px #000",
    bold: "color:#ffe500;font-weight:900;text-transform:uppercase;-webkit-text-stroke:1px #000;text-shadow:0 0 4px #000",
    boxed: "color:#fff;font-weight:700;background:rgba(0,0,0,.65);padding:2px 6px;border-radius:3px",
  };
  return `
    <div class="section">
      ${sw("p.captions.enabled", "Add captions to my video", "Words appear on screen as people speak", cap.enabled)}
      <p class="hint">Speech is turned into text <b>on this computer</b>. Nothing is uploaded. The first time, a speech model (about 150 MB) is downloaded.</p>
    </div>
    ${cap.enabled ? `
    <div class="section">
      <div class="section-title">Look</div>
      <div class="choice-grid">${S.catalog.caption_styles.map((s) => `<button class="choice ${cap.style === s.id ? "selected" : ""}" data-capstyle="${s.id}" style="background:#30364a">
        <span class="t" style="font-size:11px;color:var(--muted)">${esc(s.name)}</span>
        <span style="font-size:13px;${styleCss[s.id] || ""}">${sample[s.id] || "Caption"}</span></button>`).join("")}</div>
      <div class="section-title">Size</div>
      ${segmented("cap-size", [["small", "Small"], ["medium", "Medium"], ["large", "Large"]], cap.size)}
      <div class="section-title">Speech recognition</div>
      ${segmented("cap-model", Object.entries(MODEL_NAMES), cap.model)}
      <p class="hint">Fast is quickest. Accurate is best for accents or noisy audio, but slower.</p>
    </div>` : ""}
    <div class="section">
      <div class="section-title">Clips with speech <span class="grow"></span>
        ${sources.some((m) => !m.has_captions && !captionJob(m.id)) ? `<button class="icon-btn" id="makeAllCaptions" title="Make captions for all">${icon("sparkles", "sm")}Make all</button>` : ""}</div>
      ${sources.length ? `<div class="list">${sources.map((m) => {
        const job = captionJob(m.id);
        const status = job ? `<div class="bar" style="margin-top:5px"><div style="width:${job.progress * 100}%"></div></div><div class="sub">${esc(job.message)}</div>`
          : m.has_captions ? `<div class="sub" style="color:var(--ok)">Captions ready</div>` : `<div class="sub">No captions yet</div>`;
        const action = job ? `<button class="icon-btn" data-cancel-job="${job.id}" title="Stop">${icon("x", "sm")}</button>`
          : m.has_captions ? `<button data-edit-captions="${m.id}">${icon("edit", "sm")}Edit</button>`
          : `<button data-make-captions="${m.id}">Make</button>`;
        return `<div class="list-item"><div class="mini-thumb" style="background-image:url('/api/thumb/${m.id}')"></div>
          <div class="grow"><div class="name">${esc(m.name)}</div>${status}</div>${action}</div>`;
      }).join("")}</div>` : `<p class="hint">Clips you add that have sound will show up here.</p>`}
      ${cap.enabled && sources.some((m) => !m.has_captions) ? `<p class="hint">Clips without captions yet are done automatically when you preview or export.</p>` : ""}
    </div>`;
}
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#panel")) return;
  try {
    const st = e.target.closest("[data-capstyle]");
    if (st) { commit((p) => { p.captions.style = st.dataset.capstyle; }); return renderPanel(); }
    const make = e.target.closest("[data-make-captions]");
    if (make) return startCaptions([make.dataset.makeCaptions]);
    if (e.target.closest("#makeAllCaptions")) return startCaptions(speechSources().filter((m) => !m.has_captions).map((m) => m.id));
    const edit = e.target.closest("[data-edit-captions]");
    if (edit) return editCaptions(edit.dataset.editCaptions);
    const cancel = e.target.closest("[data-cancel-job]");
    if (cancel) return api(`jobs/${cancel.dataset.cancelJob}/cancel`, { method: "POST" });
  } catch (err) { fail(err); }
});
async function startCaptions(ids) {
  for (const id of ids) {
    const job = await api(`captions/${id}`, { method: "POST", body: { model: S.project.captions.model, language: S.project.captions.language } });
    S.jobs.set(job.id, job);
  }
  renderPanel(); pollJobs();
}
async function editCaptions(mid) {
  const m = S.byId.get(mid);
  const data = await api(`captions/${mid}`);
  const lines = data.lines;
  const row = (l, i) => `<div class="caption-line" data-i="${i}">
      <span class="t" data-seek="${l.start}" title="Play from here">${fmt(l.start)}.${String(Math.floor((l.start % 1) * 10))}</span>
      <input type="text" value="${esc(l.text)}" data-line="${i}">
      <button class="icon-btn" data-del-line="${i}" title="Delete line">${icon("trash", "sm")}</button></div>`;
  openModal(`<header>${icon("cc")}<h2>Captions · ${esc(m.name)}</h2></header>
    <div class="body">
      <video id="capVideo" src="${mediaSrc(m)}" controls style="width:100%;max-height:220px;background:#000;border-radius:10px"></video>
      <p class="hint">Fix any words the speech recognition got wrong. Click a time to hear that line.</p>
      <div class="list" id="capLines">${lines.map(row).join("")}</div>
    </div>
    <footer><button id="capRedo" title="Throw these away and transcribe again">${icon("refresh", "sm")}Start over</button><span class="spacer"></span>
      <button id="capCancel">Cancel</button><button class="primary" id="capSave">Save captions</button></footer>`);
  playWithFallback($("capVideo"), m);
  $("capLines").oninput = (e) => { const i = e.target.dataset.line; if (i != null) lines[i].text = e.target.value; };
  $("capLines").onclick = (e) => {
    const seek = e.target.closest("[data-seek]");
    if (seek) { const v = $("capVideo"); v.currentTime = Number(seek.dataset.seek); v.play(); }
    const del = e.target.closest("[data-del-line]");
    if (del) { lines[del.dataset.delLine].text = ""; del.parentElement.hidden = true; }
  };
  $("capCancel").onclick = closeModal;
  $("capSave").onclick = async () => {
    try { await api(`captions/${mid}`, { method: "PUT", body: { ...data, lines } }); closeModal(); toast("Captions saved"); markChanged(); }
    catch (err) { fail(err); }
  };
  $("capRedo").onclick = async () => {
    if (!(await confirmModal("Transcribe again?", "Your edits to these captions will be replaced.", "Transcribe again"))) return;
    await api(`captions/${mid}`, { method: "PUT", body: { lines: [] } });
    await startCaptions([mid]);
  };
}
// Captions live outside the project, so a change to them needs a nudge to mark the preview as old.
function markChanged() { S.previewSig = null; renderPlayerStatus(); }

// ----- export tab
function exportPanel() {
  const p = S.project, c = S.catalog, o = p.output;
  const job = S.exportJob && S.jobs.get(S.exportJob);
  const qualities = Object.entries(c.qualities).map(([k, v]) => [v, k[0].toUpperCase() + k.slice(1)]);
  const nearest = qualities.reduce((a, b) => (Math.abs(b[0] - o.crf) < Math.abs(a[0] - o.crf) ? b : a))[0];
  let status = "";
  if (job?.status === "running") {
    const elapsed = Date.now() / 1000 - job.started;
    const eta = job.progress > 0.03 ? ` · about ${fmt(elapsed / job.progress - elapsed)} left` : "";
    status = `<div class="progress-card"><div class="top"><b>Exporting…</b><span>${Math.round(job.progress * 100)}%</span></div>
      <div class="bar"><div style="width:${job.progress * 100}%"></div></div><div class="msg">${esc(job.message)}${eta}</div>
      <button data-cancel-job="${job.id}" class="danger">${icon("x", "sm")}Cancel export</button></div>`;
  } else if (job?.status === "done") {
    const r = job.result;
    status = `<div class="result-card"><div class="title">${icon("check")} Your video is ready</div>
      <div class="hint"><b>${esc(r.name)}</b> · ${fmt(r.duration)} · ${(r.size / 1e6).toFixed(1)} MB</div>
      <div class="actions">
        <button data-play-job="${job.id}">${icon("play", "sm")}Watch</button>
        <button data-reveal-job="${job.id}">${icon("folder", "sm")}Show in folder</button>
        ${S.desktop ? "" : `<a href="/api/jobs/${job.id}/file?download=1"><button>${icon("download", "sm")}Download</button></a>`}
      </div>
      ${r.chapters ? `<label class="field"><span>Chapters for your YouTube description</span>
        <textarea rows="4" readonly id="chaptersText">${esc(r.chapters)}</textarea></label>
        <button id="copyChapters">${icon("copy", "sm")}Copy chapters</button>` : ""}
      ${r.captions_file ? `<p class="hint">Captions were also saved as <b>${esc(r.captions_file.split(/[\\/]/).pop())}</b>. Upload it to YouTube for subtitles viewers can turn on and off.</p>` : ""}
    </div>`;
  } else if (job?.status === "error") {
    status = `<div class="error-card"><b>The export didn't finish.</b>\n${esc(job.error)}</div>`;
  } else if (job?.status === "cancelled") {
    status = `<p class="hint">Export cancelled.</p>`;
  }
  return `
    <div class="section">
      <div class="section-title">${icon("export", "sm")} Where is it going?</div>
      <div class="choice-grid">${c.exports.map((x) => `<button class="choice ${p.export_preset === x.id ? "selected" : ""}" data-export="${x.id}">
        <span class="t">${x.icon} ${esc(x.name)}</span><span class="d">${esc(x.detail)}</span></button>`).join("")}</div>
      <p class="hint">Size: <b>${o.width}×${o.height}</b>. Clips with a different shape are ${o.fit === "cover" ? "cropped to fill" : o.fit === "stretch" ? "stretched" : "shown with black bars"}.</p>
      ${segmented("fit", [["contain", "Black bars"], ["cover", "Crop to fill"]], o.fit === "stretch" ? null : o.fit)}
    </div>
    <div class="section">
      <div class="section-title">Quality</div>
      ${segmented("quality", qualities, nearest)}
    </div>
    <div class="section">
      <label class="field"><span>File name</span><input type="text" id="exportName" value="${esc(S.exportName)}"></label>
      <div class="field"><span>Save to</span>
        <div class="list-item"><span style="color:var(--muted)">${icon("folder")}</span><div class="grow"><div class="name" title="${esc(S.settings.export_dir)}">${esc(S.settings.export_dir)}</div></div>
          ${S.desktop ? `<button id="changeFolder">Change</button>` : ""}<button id="openFolder" class="icon-btn" title="Open folder">${icon("right", "sm")}</button></div>
      </div>
    </div>
    <button class="primary big" id="startExport" ${job?.status === "running" || !p.clips.length ? "disabled" : ""}>${icon("export")}Export video</button>
    ${p.captions.enabled ? `<p class="hint">${icon("cc", "sm")} Captions are on. Any clips not transcribed yet will be done first.</p>` : ""}
    ${status}`;
}
document.addEventListener("input", (e) => { if (e.target.id === "exportName") S.exportName = e.target.value; });
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#panel")) return;
  try {
    const x = e.target.closest("[data-export]");
    if (x) {
      const preset = S.catalog.exports.find((p) => p.id === x.dataset.export);
      commit((p) => { Object.assign(p, applyPreset(p, preset)); });
      renderPanel(); return;
    }
    if (e.target.closest("#startExport")) return startExport();
    if (e.target.closest("#changeFolder")) {
      const folder = await window.pywebview.api.pick_folder();
      if (folder) { S.settings = await api("settings", { method: "POST", body: { export_dir: folder } }); renderPanel(); }
      return;
    }
    if (e.target.closest("#openFolder")) return api("reveal-exports", { method: "POST" });
    const play = e.target.closest("[data-play-job]");
    if (play) { S.player = { mode: "job", jobId: play.dataset.playJob }; renderPlayer(); return; }
    const reveal = e.target.closest("[data-reveal-job]");
    if (reveal) return api(`jobs/${reveal.dataset.revealJob}/reveal`, { method: "POST" });
    if (e.target.closest("#copyChapters")) { await navigator.clipboard.writeText($("chaptersText").value); toast("Chapters copied"); }
  } catch (err) { fail(err); }
});
async function startExport() {
  await saveNow();
  try {
    const job = await api("export", { method: "POST", body: { ...S.project, filename: S.exportName || S.project.name } });
    S.jobs.set(job.id, job); S.exportJob = job.id;
    renderPanel(); renderTopbar(); pollJobs();
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- inspector
function renderInspector() {
  const el = $("inspector");
  const p = S.project;
  if (S.selected == null || !p.clips[S.selected]) {
    const { total } = layout();
    const captionsOn = p.captions.enabled ? `On · ${S.catalog.caption_styles.find((s) => s.id === p.captions.style)?.name}` : "Off";
    el.innerHTML = `<h3>${icon("film")} ${esc(p.name || "Your video")}</h3><div class="sub">Select a clip on the timeline to change it.</div>
      <div class="section">
        <div class="stat"><span>Length</span><b>${fmt(total)}</b></div>
        <div class="stat"><span>Clips</span><b>${p.clips.length}</b></div>
        <div class="stat"><span>Size</span><b>${p.output.width}×${p.output.height}</b></div>
        <div class="stat"><span>Transitions</span><b>${p.transition ? `${STYLE_NAMES[p.transition_style]} · ${secs(p.transition)}` : "Cuts"}</b></div>
        <div class="stat"><span>Music</span><b>${p.music ? esc(p.music.path.split(/[\\/]/).pop()) : "None"}</b></div>
        <div class="stat"><span>Captions</span><b>${captionsOn}</b></div>
      </div>
      <div class="section kbd">
        <div><kbd>Space</kbd> play / pause</div><div><kbd>Ctrl</kbd>+<kbd>Z</kbd> undo · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> redo</div>
        <div><kbd>Delete</kbd> remove clip · <kbd>Ctrl</kbd>+<kbd>D</kbd> duplicate</div><div><kbd>←</kbd> <kbd>→</kbd> select previous / next clip</div>
      </div>`;
    return;
  }
  const i = S.selected, c = p.clips[i], m = mediaFor(c), { durs } = layout();
  const kind = c.type === "title" ? ["text", "Title card"] : c.type === "image" || m?.is_image ? ["image", "Photo"] : m?.is_audio ? ["music", "Audio"] : ["film", "Video clip"];
  let body = "";
  if (c.type === "title") {
    body += `<div class="section"><div class="section-title">Text</div>
      <textarea rows="3" data-bind="c.text">${esc(c.text)}</textarea>
      <div class="grid2"><label class="field"><span>Text colour</span><input type="color" data-bind="c.color" value="${toHex(c.color, "#ffffff")}"></label>
      <label class="field"><span>Background</span><input type="color" data-bind="c.background" value="${toHex(c.background, "#000000")}"></label></div>
      <div class="section-title">Text size</div>
      ${segmented("title-size", [[null, "Auto"], [Math.round(p.output.height / 18), "S"], [Math.round(p.output.height / 12), "M"], [Math.round(p.output.height / 8), "L"]], c.font_size)}
      <label class="field"><span>Show for</span>${slider("c.duration", c.duration || 4, 1, 15, 0.5)}</label></div>`;
  } else if (c.type === "image" || m?.is_image) {
    body += `<div class="section"><label class="field"><span>Show for</span>${slider("c.duration", c.duration || 5, 1, 30, 0.5)}</label></div>`;
  } else if (m) {
    body += `<div class="section"><div class="section-title">${icon("film", "sm")} Trim</div>
      <div class="grid2"><label class="field"><span>Start at (s)</span><input type="number" min="0" step="0.1" data-bind="c.start" data-kind="number" value="${+(c.start || 0).toFixed(2)}"></label>
      <label class="field"><span>End at (s)</span><input type="number" min="0" step="0.1" data-bind="c.end" data-kind="maybe-number" placeholder="${(m.duration || 0).toFixed(1)}" value="${c.end != null ? +c.end.toFixed(2) : ""}"></label></div>
      <p class="hint">Plays ${secs(durs[i])} of ${secs(m.duration || 0)}. Tip: drag the edges of the clip on the timeline.</p></div>
      <div class="section"><div class="section-title">${icon("volume", "sm")} Sound</div>
      ${m.has_audio ? `${slider("c.volume", c.volume, 0, 2, 0.05, "pct")}${sw("c.mute", "Mute this clip", "", c.mute)}` : `<p class="hint">This clip has no sound.</p>`}
      ${hasSpeech(c) || (m.has_audio && c.mute) ? sw("c.captions", "Captions for this clip", p.captions.enabled ? "" : "Turn captions on in the Captions tab", c.captions !== false) : ""}</div>`;
  } else {
    body += `<div class="notice warn">${icon("info")}<div><b>File missing.</b> ${esc(c.path)} was moved or deleted. Add it to the library again, or remove this clip.</div></div>`;
  }
  if (i > 0) {
    const tLen = [[null, "Default"], [0, "Cut"], [0.5, "0.5s"], [1, "1s"], [2, "2s"]];
    body += `<div class="section"><div class="section-title">${icon("swap", "sm")} Transition into this clip</div>
      ${segmented("clip-transition", tLen, tLen.some(([v]) => v === c.transition) ? c.transition : undefined)}
      <select data-bind="c.transition_style" data-kind="maybe-text"><option value="">Style: project default (${STYLE_NAMES[p.transition_style]})</option>
        ${S.catalog.transition_styles.map((s) => `<option value="${s}" ${c.transition_style === s ? "selected" : ""}>${STYLE_NAMES[s] || s}</option>`).join("")}</select></div>`;
  }
  body += `<div class="section"><div class="section-title">Fade from / to black</div>
    <label class="field"><span>Fade in</span>${slider("c.fade_in", c.fade_in, 0, 3, 0.1)}</label>
    <label class="field"><span>Fade out</span>${slider("c.fade_out", c.fade_out, 0, 3, 0.1)}</label></div>
    <div class="section"><div class="section-title">${icon("bookmark", "sm")} Chapter</div>
    <input type="text" data-bind="c.chapter" data-kind="maybe-text" placeholder="e.g. Day 1: arriving" value="${esc(c.chapter || "")}"></div>
    <div class="actions">
      <button class="icon-btn" data-clip-act="left" title="Move earlier" ${i === 0 ? "disabled" : ""}>${icon("left")}</button>
      <button class="icon-btn" data-clip-act="right" title="Move later" ${i === p.clips.length - 1 ? "disabled" : ""}>${icon("right")}</button>
      <span class="spacer"></span>
      <button data-clip-act="dup">${icon("copy", "sm")}Duplicate</button>
      <button class="danger" data-clip-act="del">${icon("trash", "sm")}Remove</button></div>`;
  el.innerHTML = `<h3>${icon(kind[0])} ${esc(kind[1])}</h3><div class="sub">${esc(c.type === "title" ? `Clip ${i + 1}` : clipName(c))}</div>${body}`;
}
function toHex(color, fallback) {
  if (/^#[0-9a-f]{6}$/i.test(color || "")) return color;
  return { white: "#ffffff", black: "#000000" }[color] || fallback;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-clip-act]"); if (!b) return;
  clipAction(b.dataset.clipAct);
});
function clipAction(act) {
  const i = S.selected; if (i == null) return;
  if (act === "del") { commit((p) => { p.clips.splice(i, 1); }); S.selected = S.project.clips.length ? Math.min(i, S.project.clips.length - 1) : null; }
  if (act === "dup") { commit((p) => { p.clips.splice(i + 1, 0, clone(p.clips[i])); }); S.selected = i + 1; }
  if (act === "left" && i > 0) { commit((p) => { [p.clips[i - 1], p.clips[i]] = [p.clips[i], p.clips[i - 1]]; }); S.selected = i - 1; }
  if (act === "right" && i < S.project.clips.length - 1) { commit((p) => { [p.clips[i + 1], p.clips[i]] = [p.clips[i], p.clips[i + 1]]; }); S.selected = i + 1; }
  renderInspector(); renderPlayer(); renderTimeline();
}

// ---------------------------------------------------------------- player
// What the preview depends on: quality, speed and the file name don't change how it looks.
function previewSignature() {
  const p = clone(S.project);
  delete p.name; delete p.export_preset; delete p.output.crf; delete p.output.preset; delete p.output.audio_bitrate;
  return JSON.stringify(p);
}
function currentPreviewJob() { const j = S.previewJob && S.jobs.get(S.previewJob); return j; }
async function startPreview() {
  if (!S.project.clips.length) return;
  await saveNow();
  try {
    const job = await api("preview", { method: "POST", body: S.project });
    S.jobs.set(job.id, job); S.previewJob = job.id; S.previewSig = previewSignature();
    S.player = { mode: "preview", jobId: job.id };
    renderPlayer(); pollJobs();
  } catch (err) { fail(err); }
}
function renderPlayer() {
  const el = $("player");
  const p = S.project;
  const job = S.player.mode === "preview" ? currentPreviewJob() : S.player.mode === "job" ? S.jobs.get(S.player.jobId) : null;
  if (!p.clips.length && !job) {
    el.innerHTML = `<div class="welcome"><h1>Let's make a video</h1><div>Put any clips together into one long video: vlogs, podcasts, trips, tutorials.</div>
      <div class="steps">
        <div class="step"><span class="n">1</span><b>Add your clips</b>Videos, photos and music, any format.</div>
        <div class="step"><span class="n">2</span><b>Arrange</b>Drag to reorder, drag edges to trim.</div>
        <div class="step"><span class="n">3</span><b>Style</b>One-click looks, transitions, captions.</div>
        <div class="step"><span class="n">4</span><b>Export</b>For YouTube, Shorts, TikTok and more.</div>
      </div><button class="primary" id="welcomeAdd" style="margin-top:10px">${icon("plus")}Add videos</button></div>`;
    $("welcomeAdd").onclick = pickFiles;
    $("transport").innerHTML = "";
    return;
  }
  if (job) {
    if (job.status === "running") {
      el.innerHTML = `<div class="player-overlay"><b>${job.kind === "preview" ? "Building your preview…" : "Working…"}</b>
        <div class="bar"><div style="width:${job.progress * 100}%"></div></div><div class="hint">${esc(job.message)}</div>
        <button data-cancel-job="${job.id}">${icon("x", "sm")}Stop</button></div>`;
    } else if (job.status === "done") {
      el.innerHTML = `<video id="fullVideo" src="/api/jobs/${job.id}/file" controls autoplay></video>
        <span class="badge pill accent">${job.kind === "preview" ? "Preview (low resolution)" : "Exported video"}</span>
        <span class="stale" id="staleBadge"></span>`;
      const v = $("fullVideo");
      v.addEventListener("timeupdate", () => updatePlayhead(v.currentTime));
      v.addEventListener("play", renderTransport); v.addEventListener("pause", renderTransport);
    } else {
      el.innerHTML = `<div class="player-overlay"><b>${job.status === "error" ? "The preview didn't work" : "Stopped"}</b>
        <div class="hint" style="max-width:420px;white-space:pre-wrap">${esc(job.error || "")}</div>
        <button id="backToClips">Back to clips</button></div>`;
      $("backToClips").onclick = () => { S.player = { mode: "auto" }; renderPlayer(); };
    }
    renderPlayerStatus(); renderTransport();
    return;
  }
  // Show the selected clip (or the first one).
  const i = S.selected ?? 0, c = p.clips[i], m = mediaFor(c);
  const ar = `${p.output.width} / ${p.output.height}`;
  if (c.type === "title") {
    const fs = c.font_size ? c.font_size / p.output.height * 100 : 100 / 12;
    el.innerHTML = `<div class="title-card" style="aspect-ratio:${ar};height:100%;background:${esc(toHex(c.background, "#000"))};color:${esc(toHex(c.color, "#fff"))};font-size:${fs}cqh;container-type:size">${esc(c.text)}</div>`;
    const card = el.firstElementChild;
    card.style.fontSize = (card.clientHeight * fs / 100) + "px";
  } else if (!m || m.missing) {
    el.innerHTML = `<div class="welcome">${icon("info", "lg")}<div>This file is missing.</div></div>`;
  } else if (m.is_image) {
    el.innerHTML = `<img src="/api/media/${m.id}" alt="" onerror="this.onerror=null;this.src='/api/thumb/${m.id}'">`;
  } else {
    const proxyJob = [...S.jobs.values()].find((j) => j.kind === "proxy" && j.result?.key === m.id && j.status === "running");
    if (proxyJob) {
      el.innerHTML = `<div class="player-overlay"><b>Getting this clip ready to play…</b>
        <div class="bar"><div style="width:${proxyJob.progress * 100}%"></div></div>
        <div class="hint">This format can't play here directly, so a quick preview copy is being made. Your export uses the original.</div></div>`;
      renderTransport();
      return;
    }
    el.innerHTML = `<video id="clipVideo" src="${mediaSrc(m)}#t=${c.start || 0}" ${m.has_video ? "" : `poster="/api/thumb/${m.id}"`} preload="auto"></video>`;
    const v = $("clipVideo");
    playWithFallback(v, m, c.start || 0);
    const end = (c.start || 0) + layout().durs[i];
    v.addEventListener("timeupdate", () => { if (v.currentTime >= end) { v.pause(); v.currentTime = c.start || 0; } renderTransportTime(); });
    v.addEventListener("play", renderTransport); v.addEventListener("pause", renderTransport);
    v.addEventListener("click", () => (v.paused ? v.play() : v.pause()));
  }
  el.insertAdjacentHTML("beforeend", `<span class="badge pill">Clip ${i + 1} of ${p.clips.length}</span>`);
  renderTransport();
}
function renderPlayerStatus() {
  const badge = $("staleBadge"); if (!badge) return;
  const stale = S.player.mode === "preview" && S.previewSig !== previewSignature();
  badge.innerHTML = stale ? `<button class="secondary" id="refreshPreview">${icon("refresh", "sm")}Out of date. Refresh</button>` : "";
  if (stale) $("refreshPreview").onclick = startPreview;
}
function activeVideo() { return $("fullVideo") || $("clipVideo"); }
function renderTransport() {
  const v = activeVideo();
  if (!v) { $("transport").innerHTML = S.project.clips.length ? `<span class="hint">Press <b>Preview</b> to watch the whole video with transitions, music and captions.</span>` : ""; return; }
  const full = !!$("fullVideo");
  $("transport").innerHTML = `<button class="icon-btn" id="playBtn" title="Play / pause (Space)">${icon(v.paused ? "play" : "pause")}</button>
    <span class="time" id="tTime"></span><span class="spacer"></span>
    ${full ? `<button class="ghost" id="backToClipsBtn">${icon("film", "sm")}Show clips</button>`
      : `<span class="hint">Showing one clip. Press <b>Preview</b> to watch everything together.</span>`}`;
  $("playBtn").onclick = () => (v.paused ? v.play() : v.pause());
  if (full) $("backToClipsBtn").onclick = () => { S.player = { mode: "auto" }; renderPlayer(); updatePlayhead(null); };
  renderTransportTime();
}
function renderTransportTime() {
  const v = activeVideo(), t = $("tTime"); if (!v || !t) return;
  if ($("fullVideo")) { t.innerHTML = `<b>${fmt(v.currentTime)}</b> / ${fmt(v.duration)}`; return; }
  const c = S.project.clips[S.selected ?? 0];
  t.innerHTML = `<b>${fmt(Math.max(0, v.currentTime - (c?.start || 0)))}</b> / ${fmt(layout().durs[S.selected ?? 0])}`;
}

// ---------------------------------------------------------------- timeline
function fitTimeline() {
  const { total } = layout();
  const width = $("timelineScroll").clientWidth - 60;
  S.zoom = clamp(total ? width / total : 40, 0.5, 200);
  renderTimeline();
}
function renderTimeline() {
  if (!S.project) return;
  const L = layout(), { durs, overlaps, total } = L, geo = geometry(L);
  const pps = S.zoom;
  $("timelineTools").innerHTML = `
    <span class="total">Total <b>${fmt(total)}</b> · ${S.project.clips.length} clip${S.project.clips.length === 1 ? "" : "s"}</span>
    <span class="spacer"></span>
    <button class="ghost" id="tlTitle">${icon("text", "sm")}Title</button>
    <button class="ghost" id="tlAdd">${icon("plus", "sm")}Add media</button>
    <span style="color:var(--muted)">${icon("zoom", "sm")}</span>
    <input type="range" id="zoom" min="0" max="100" value="${Math.round(Math.log(S.zoom / 0.5) / Math.log(400) * 100)}" title="Zoom">
    <button class="icon-btn" id="tlFit" title="Fit the whole video">${icon("fit", "sm")}</button>`;
  const width = Math.max(total * pps + 200, $("timelineScroll").clientWidth);
  const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600].find((s) => s * pps >= 80) || 3600;
  let ticks = "";
  for (let t = 0; t <= total + step; t += step) ticks += `<div class="tick" style="left:${t * pps}px">${fmt(t)}</div>`;
  let blocks = "";
  S.project.clips.forEach((c, i) => {
    const m = mediaFor(c), w = Math.max(18, (geo[i].b - geo[i].a) * pps - 2);
    const missing = c.type !== "title" && (!m || m.missing);
    const bg = c.type === "title" ? `background:${esc(toHex(c.background, "#000"))};` : m && !missing ? `background-image:url('/api/filmstrip/${m.id}');` : "";
    const icons = [
      c.chapter ? icon("bookmark", "sm") : "", c.mute ? icon("mute", "sm") : "",
      S.project.captions.enabled && hasSpeech(c) && c.captions !== false ? icon("cc", "sm") : "",
    ].join("");
    blocks += `<div class="clip-block ${c.type === "title" ? "title" : ""} ${missing ? "missing" : ""} ${S.selected === i ? "selected" : ""}"
        data-i="${i}" style="left:${geo[i].a * pps + 1}px;width:${w}px;${bg}" title="${esc(clipName(c))}">
      <div class="handle l" data-handle="l"></div><div class="handle r" data-handle="r"></div>
      <div class="label" style="${c.type === "title" ? `color:${esc(toHex(c.color, "#fff"))}` : ""}">${esc(clipName(c))}</div>
      <div class="info">${w > 60 ? fmt(durs[i]) : ""} ${w > 90 ? icons : ""}</div></div>`;
    if (i > 0) {
      const x = geo[i].a * pps;
      const style = c.transition_style || S.project.transition_style;
      blocks += overlaps[i] > 0
        ? `<button class="transition-badge" data-transition="${i}" style="left:${x}px" title="${STYLE_NAMES[style]} · ${secs(overlaps[i])}">${icon("swap", "sm")}${secs(overlaps[i])}</button>`
        : `<button class="transition-badge cut" data-transition="${i}" style="left:${x}px" title="Add a transition here">${icon("plus", "sm")}</button>`;
    }
  });
  $("timeline").style.width = width + "px";
  $("timeline").innerHTML = `<div class="ruler" id="ruler">${ticks}</div>
    ${S.project.clips.length ? `<div class="track" id="track" style="width:${total * pps}px">${blocks}</div>`
      : `<div class="timeline-empty" id="track">${icon("plus")}Drag clips here from the library, or click <b>Add media</b></div>`}
    <div class="playhead" id="playhead" hidden></div>`;
  $("zoom").oninput = (e) => { S.autoFit = false; S.zoom = 0.5 * Math.pow(400, e.target.value / 100); renderTimeline(); };
  $("tlFit").onclick = () => { S.autoFit = true; fitTimeline(); };
  $("tlAdd").onclick = pickFiles;
  $("tlTitle").onclick = () => addClipAt(newClip(null));
  const v = $("fullVideo"); if (v) updatePlayhead(v.currentTime);
}
function updatePlayhead(t) {
  const ph = $("playhead"); if (!ph) return;
  if (t == null || !$("fullVideo")) { ph.hidden = true; return; }
  ph.hidden = false; ph.style.left = 14 + t * S.zoom + "px";
  renderTransportTime();
}
function selectClip(i) {
  if ($("inspector").contains(document.activeElement)) document.activeElement.blur();
  S.selected = i;
  if (S.player.mode !== "preview" && S.player.mode !== "job") S.player.mode = "auto";
  renderInspector(); renderTimeline();
  if (!$("fullVideo")) renderPlayer();
}

function setupTimelineEvents() {
  const scroll = $("timelineScroll");
  let drag = null;

  scroll.addEventListener("pointerdown", (e) => {
    const badge = e.target.closest("[data-transition]");
    if (badge) {
      const i = Number(badge.dataset.transition);
      if (!(S.project.clips[i].transition ?? S.project.transition)) commit((p) => { p.clips[i].transition = 0.5; });
      selectClip(i);
      return;
    }
    const ruler = e.target.closest("#ruler");
    if (ruler) {
      const t = (e.clientX - ruler.getBoundingClientRect().left) / S.zoom;
      const v = $("fullVideo");
      if (v) { v.currentTime = t; return; }
      const i = geometry().findIndex((g) => t >= g.a && t < g.b);
      if (i >= 0) selectClip(i);
      return;
    }
    const block = e.target.closest(".clip-block");
    if (!block) { if (e.target.closest("#track")) { S.selected = null; renderInspector(); renderTimeline(); } return; }
    const i = Number(block.dataset.i);
    const handle = e.target.closest("[data-handle]")?.dataset.handle;
    const c = S.project.clips[i];
    drag = { i, handle, x0: e.clientX, moved: false, orig: clone(c), m: mediaFor(c) };
    e.preventDefault();
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x0;
    if (!drag.moved && Math.abs(dx) < 4) return;
    if (!drag.moved) {
      drag.moved = true;
      if (drag.handle) commit(() => {}, null);  // one undo step for the whole trim
      selectClip(drag.i);
    }
    const dt = dx / S.zoom, c = S.project.clips[drag.i], o = drag.orig;
    if (drag.handle) {
      const isVideo = c.type === "video" && drag.m && !drag.m.is_image;
      if (isVideo && drag.handle === "l") {
        const end = o.end ?? drag.m.duration;
        c.start = clamp((o.start || 0) + dt, 0, end - 0.2);
      } else if (isVideo) {
        const end = clamp((o.end ?? drag.m.duration) + dt, (o.start || 0) + 0.2, drag.m.duration);
        c.end = end >= drag.m.duration - 0.05 ? null : end;
        if (c.duration) c.duration = null;
      } else {
        const sign = drag.handle === "l" ? -1 : 1;
        c.duration = clamp(Math.round(((o.duration || clipDuration(o)) + sign * dt) * 10) / 10, 0.5, 600);
      }
      renderTimeline();
    } else {
      // Reordering: find where the clip would land.
      const track = $("track").getBoundingClientRect();
      const to = dropIndexAt((e.clientX - track.left) / S.zoom);
      drag.to = to;
      document.querySelectorAll(".insert-marker").forEach((n) => n.remove());
      const x = seamX(to);
      $("track").insertAdjacentHTML("beforeend", `<div class="insert-marker" style="left:${x - 1}px"></div>`);
      document.querySelector(`.clip-block[data-i="${drag.i}"]`)?.classList.add("dragging");
    }
  });
  window.addEventListener("pointerup", () => {
    if (!drag) return;
    const d = drag; drag = null;
    document.querySelectorAll(".insert-marker").forEach((n) => n.remove());
    if (!d.moved) return selectClip(d.i);
    if (d.handle) { afterChange({ panels: true }); renderPlayer(); return; }
    let to = d.to ?? d.i;
    if (to > d.i) to--;
    if (to !== d.i) { commit((p) => { const [c] = p.clips.splice(d.i, 1); p.clips.splice(to, 0, c); }); S.selected = to; }
    renderTimeline(); renderInspector();
  });

  // Drag from the library onto the timeline.
  scroll.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("application/x-longform-media")) return;
    e.preventDefault();
    const track = $("track");
    if (!S.project.clips.length) { track.classList.add("over"); return; }
    const to = dropIndexAt((e.clientX - track.getBoundingClientRect().left) / S.zoom);
    scroll.dataset.dropAt = to;
    document.querySelectorAll(".insert-marker").forEach((n) => n.remove());
    const x = seamX(to);
    track.insertAdjacentHTML("beforeend", `<div class="insert-marker" style="left:${x - 1}px"></div>`);
  });
  scroll.addEventListener("dragleave", () => { document.querySelectorAll(".insert-marker").forEach((n) => n.remove()); $("track")?.classList.remove("over"); });
  scroll.addEventListener("drop", (e) => {
    const id = e.dataTransfer.getData("application/x-longform-media"); if (!id) return;
    e.preventDefault();
    document.querySelectorAll(".insert-marker").forEach((n) => n.remove());
    const m = S.byId.get(id);
    if (m.is_audio) { commit((p) => { p.music = { path: m.path, volume: 0.25, duck: true, fade_out: 3 }; }); toast(`“${m.name}” set as background music`); return; }
    addClipAt(newClip(m), S.project.clips.length ? Number(scroll.dataset.dropAt ?? S.project.clips.length) : 0);
  });
  window.addEventListener("resize", () => { if (S.autoFit) fitTimeline(); else renderTimeline(); });
}

// ---------------------------------------------------------------- keyboard
function setupKeyboard() {
  window.addEventListener("keydown", (e) => {
    const typing = e.target.matches("input[type=text], input[type=number], textarea, select");
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (mod && e.key.toLowerCase() === "y" && !typing) { e.preventDefault(); redo(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); saveNow(); return; }
    if (typing || !$("modal").hidden) { if (e.key === "Escape") closeModal(); return; }
    if (e.key === " ") { e.preventDefault(); const v = activeVideo(); if (v) v.paused ? v.play() : v.pause(); }
    if ((e.key === "Delete" || e.key === "Backspace") && S.selected != null) { e.preventDefault(); clipAction("del"); }
    if (mod && e.key.toLowerCase() === "d" && S.selected != null) { e.preventDefault(); clipAction("dup"); }
    if (e.key === "ArrowRight" && S.project.clips.length) selectClip(Math.min((S.selected ?? -1) + 1, S.project.clips.length - 1));
    if (e.key === "ArrowLeft" && S.project.clips.length) selectClip(Math.max((S.selected ?? 1) - 1, 0));
    if (e.key === "Escape") { S.selected = null; renderInspector(); renderTimeline(); }
  });
}

// ---------------------------------------------------------------- jobs
let pollTimer = null;
async function pollJobs() {
  clearTimeout(pollTimer);
  const running = [...S.jobs.values()].filter((j) => j.status === "running");
  if (!running.length) return;
  for (const old of running) {
    let job;
    try { job = await api(`jobs/${old.id}`); } catch { job = { ...old, status: "error", error: "Lost track of this job" }; }
    S.jobs.set(job.id, job);
    if (job.status !== "running") await jobFinished(job);
  }
  renderTopbar();
  if (S.tab === "export" || S.tab === "captions") {
    if (!document.activeElement || !$("panel").contains(document.activeElement) || document.activeElement.tagName === "BUTTON") renderPanel();
  }
  const pj = S.player.jobId && S.jobs.get(S.player.jobId);
  if (pj?.status === "running") renderPlayer();
  else if (!$("fullVideo") && running.some((j) => j.kind === "proxy")) renderPlayer();
  pollTimer = setTimeout(pollJobs, 700);
}
async function jobFinished(job) {
  if (job.kind === "captions") {
    await loadLibrary();
    if (job.status === "done") toast(`Captions ready: ${job.message}`);
    if (job.status === "error") toast(job.error, { error: true });
    renderPanel(); renderTimeline(); markChanged();
  }
  if (job.kind === "export") {
    await loadLibrary(); // exports may have made captions
    if (job.status === "done") {
      toast(`Exported ${job.result.name}`, { action: { label: "Show in folder", run: () => api(`jobs/${job.id}/reveal`, { method: "POST" }) } });
    }
    if (job.status === "error") toast("The export didn't finish. See the Export tab for details.", { error: true });
  }
  if (job.kind === "preview" && S.player.jobId === job.id) { await loadLibrary(); renderPlayer(); }
  if (job.kind === "proxy") {
    const mid = job.result?.key;
    if (job.status === "done") {
      S.proxies.set(mid, true);
      (S.proxyWaiters.get(mid) || []).forEach((fn) => fn());
    } else if (job.status === "error") {
      toast("This clip can't be previewed here, but it will still be included when you export.", { error: true });
    }
    S.proxyWaiters.delete(mid);
    if (!$("fullVideo")) renderPlayer();
  }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("#player [data-cancel-job]");
  if (b) api(`jobs/${b.dataset.cancelJob}/cancel`, { method: "POST" });
});

window.addEventListener("beforeunload", () => { if ($("saveState").textContent === "Saving…") saveNow(); });
document.addEventListener("DOMContentLoaded", () => init().catch(fail));
