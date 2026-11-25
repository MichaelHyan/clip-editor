// 入口：初始化各模块、加载项目、全局快捷键、文件导入、状态栏
import { api } from "./api.js";
import * as S from "./state.js";
import * as preview from "./preview.js";
import * as audio from "./audio.js";
import * as timeline from "./timeline.js";
import * as inspector from "./inspector.js";
import * as exportUi from "./export_ui.js";
import * as assetsUi from "./assets_ui.js";
import * as projectsUi from "./projects_ui.js";
import * as settingsUi from "./settings_ui.js";
import { connect, disconnect } from "./socket.js";
import { el, clear, timecode, clamp, debounce } from "./util.js";

const $ = (id) => document.getElementById(id);

let playStartWall = 0;
let playStartT = 0;
let rafPlay = 0;
let lastTickPaint = 0;

// ---------------------------------------------------------------- 启动

async function boot() {
  const params = new URLSearchParams(location.search);
  let pid = params.get("p") || localStorage.getItem("clip-editor:pid");

  preview.init();
  audio.init();
  timeline.init();
  inspector.init();
  exportUi.init();
  assetsUi.init();
  projectsUi.init();
  settingsUi.init();
  bindHeader();
  bindTransport();
  bindKeys();

  try {
    const list = await api.listProjects();
    if (pid && list.some((p) => p.id === pid)) {
      /* 使用记忆中的项目 */
    } else if (list.length) {
      pid = list[0].id;
    } else {
      const created = await api.createProject("未命名项目", null);
      pid = created.project.id;
    }
    localStorage.setItem("clip-editor:pid", pid);
    const url = new URL(location.href);
    url.searchParams.set("p", pid);
    history.replaceState(null, "", url.toString());
  } catch (e) {
    toast("无法连接后端：" + e.message, "error");
    return;
  }

  try {
    await S.loadProject(pid);
    connect(pid);
    updateHeader();
    setStatus();
    fitPreview();
  } catch (e) {
    toast("项目加载失败：" + e.message, "error");
    return;
  }

  S.bus.on("project:loaded", () => { updateHeader(); S.recomputeTotal(); });
  S.bus.on("project:changed", () => {
    updateHeader();
    fitPreview();
  });
  S.bus.on("clip:changed", () => { S.recomputeTotal(); setStatus(); });
  S.bus.on("timeline:changed", () => { updateTransport(); setStatus(); });
  S.bus.on("history:changed", () => { updateHistoryButtons(); setStatus(); });
  S.bus.on("heap", () => {});
  S.bus.on("playhead:changed", updateTransport);
  S.bus.on("playing:changed", (v) => {
    const b = $("btn-play");
    if (b) b.textContent = v ? "\u23F8" : "\u25B6";
    if (v) startClock();
    else stopClock();
  });
  S.bus.on("saving:changed", setStatus);
  S.bus.on("socket:status", setStatus);
  S.bus.on("tools:changed", setStatus);
  S.bus.on("toast", (p) => toast(p.msg, p.kind || "info"));
  S.bus.on("agent:event", (p) => {
    if (p.kind && String(p.kind).startsWith("tool.")) {
      toast("agent 更新了效果工具：" + (p.id || ""), "info");
    }
  });

  updateTransport();
  updateHistoryButtons();
  setStatus();
}

// ---------------------------------------------------------------- 播放时钟

function startClock() {
  playStartWall = performance.now();
  playStartT = S.state.playhead;
  if (playStartT >= S.state.totalDuration - 1e-3) {
    playStartT = 0;
    S.setPlayhead(0);
  }
  stopClock();
  rafPlay = requestAnimationFrame(tickClock);
}

function stopClock() {
  if (rafPlay) cancelAnimationFrame(rafPlay);
  rafPlay = 0;
}

function tickClock() {
  if (!S.state.playing) return;
  rafPlay = requestAnimationFrame(tickClock);
  const now = performance.now();
  if (now - lastTickPaint < 30) return;
  lastTickPaint = now;
  const t = playStartT + (now - playStartWall) / 1000;
  const total = S.state.totalDuration;
  if (total > 0 && t >= total) {
    S.setPlayhead(total);
    S.setPlaying(false);
    return;
  }
  S.setPlayhead(t);
}

// ---------------------------------------------------------------- 顶栏

function bindHeader() {
  $("btn-undo").addEventListener("click", () => safeRun(() => S.undo()));
  $("btn-redo").addEventListener("click", () => safeRun(() => S.redo()));
  $("btn-import").addEventListener("click", openImport);
  S.bus.on("assets:request-import", openImport);
  $("btn-snap").addEventListener("click", async () => {
    try {
      await api.snapshot(S.state.pid, "manual");
      toast("已创建快照", "ok");
    } catch (e) {
      toast("快照失败：" + e.message, "error");
    }
  });
}

function updateHeader() {
  const pj = S.state.project;
  if (!pj) return;
  $("proj-name").textContent = pj.name || "未命名项目";
  const st = pj.settings || {};
  $("proj-meta").textContent = `${st.width || 1920}x${st.height || 1080} · ${st.fps || 60}fps · ${(st.sample_rate || 48000) / 1000}kHz`;
  updateHistoryButtons();
}

function updateHistoryButtons() {
  const h = S.state.history || {};
  $("btn-undo").disabled = !(h.undo > 0);
  $("btn-redo").disabled = !(h.redo > 0);
  $("btn-undo").title = h.undo_labels && h.undo_labels.length ? "撤销：" + h.undo_labels[h.undo_labels.length - 1] : "撤销";
  $("btn-redo").title = h.redo_labels && h.redo_labels.length ? "重做：" + h.redo_labels[h.redo_labels.length - 1] : "重做";
}

function setStatus() {
  const rev = (S.state.project && S.state.project.rev) || 0;
  $("status-rev").textContent = "rev " + rev;
  const saving = S.state.saving > 0;
  $("status-save").textContent = saving ? "保存中…" : "已保存";
  $("status-save").className = "status-item " + (saving ? "busy" : "ok");
  const dot = $("status-sync");
  dot.className = "status-dot " + (S.state.ready ? "on" : "");
  dot.title = "实时同步";
}

// ---------------------------------------------------------------- 传输条

function bindTransport() {
  $("btn-play").addEventListener("click", togglePlay);
  $("btn-prev-frame").addEventListener("click", () => stepFrame(-1));
  $("btn-next-frame").addEventListener("click", () => stepFrame(1));
  $("btn-home").addEventListener("click", () => { S.setPlaying(false); S.setPlayhead(0); });
  $("btn-end").addEventListener("click", () => { S.setPlaying(false); S.setPlayhead(S.state.totalDuration); });
  const vol = $("volume");
  vol.addEventListener("input", () => audio.setMasterVolume(parseFloat(vol.value)));
  audio.setMasterVolume(parseFloat(vol.value));
}

function togglePlay() {
  S.setPlaying(!S.state.playing);
}

function stepFrame(dir) {
  S.setPlaying(false);
  const f = S.fps();
  S.setPlayhead(S.state.playhead + dir / f);
}

function updateTransport() {
  const t = S.state.playhead;
  const total = S.state.totalDuration;
  $("tc-cur").textContent = timecode(t);
  $("tc-total").textContent = timecode(total);
  const ratio = total > 0 ? clamp(t / total, 0, 1) : 0;
  const fill = $("tc-fill");
  if (fill) fill.style.width = ratio * 100 + "%";
}

function fitPreview() {
  const wrap = $("preview-wrap");
  const cv = $("preview-canvas");
  if (!wrap || !cv || !S.state.project) return;
  const st = S.state.project.settings || {};
  const aw = wrap.clientWidth - 24;
  const ah = wrap.clientHeight - 24;
  const ar = (st.width || 1920) / (st.height || 1080);
  let w = aw;
  let h = w / ar;
  if (h > ah) {
    h = ah;
    w = h * ar;
  }
  cv.style.width = Math.max(80, w) + "px";
  cv.style.height = Math.max(45, h) + "px";
}

// ---------------------------------------------------------------- 快捷键

function bindKeys() {
  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (ev.target && ev.target.isContentEditable);

    if (ev.ctrlKey || ev.metaKey) {
      const k = ev.key.toLowerCase();
      if (k === "z" && !ev.shiftKey) { ev.preventDefault(); safeRun(() => S.undo()); return; }
      if ((k === "z" && ev.shiftKey) || k === "y") { ev.preventDefault(); safeRun(() => S.redo()); return; }
      if (k === "s") { ev.preventDefault(); $("btn-snap").click(); return; }
      if (k === "o") { ev.preventDefault(); openImport(); return; }
      return;
    }
    if (typing) return;

    if (ev.code === "Space") { ev.preventDefault(); togglePlay(); return; }
    if (ev.key === "s" || ev.key === "S") { ev.preventDefault(); splitSelected(); return; }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteSelected(); return; }
    if (ev.key === "ArrowLeft") { ev.preventDefault(); stepFrame(ev.shiftKey ? -10 : -1); return; }
    if (ev.key === "ArrowRight") { ev.preventDefault(); stepFrame(ev.shiftKey ? 10 : 1); return; }
    if (ev.key === "Home") { ev.preventDefault(); S.setPlayhead(0); return; }
    if (ev.key === "End") { ev.preventDefault(); S.setPlayhead(S.state.totalDuration); return; }
    if (ev.key === "m" || ev.key === "M") { ev.preventDefault(); addTimelineMarker(); return; }
  });

  window.addEventListener("resize", debounce(fitPreview, 100));
}

async function splitSelected() {
  const cid = S.state.selectedClipId;
  if (!cid) { toast("先选中一个片段", "info"); return; }
  const t = S.state.playhead - S.clipStart(cid);
  if (t <= 0.01 || t >= S.clipDuration(cid) - 0.01) {
    toast("播放头需位于片段内部才能切分", "info");
    return;
  }
  await safeRun(async () => {
    await api.splitClip(S.state.pid, cid, t);
    await S.refresh();
    toast("已切分", "ok");
  });
}

async function deleteSelected() {
  const cid = S.state.selectedClipId;
  if (!cid) return;
  await safeRun(async () => {
    await api.delClip(S.state.pid, cid);
    S.selectClip(null);
    await S.refresh();
    toast("已删除片段", "ok");
  });
}

async function addTimelineMarker() {
  await safeRun(async () => {
    await api.addMarker(S.state.pid, S.state.playhead, "标记 " + timecode(S.state.playhead).slice(3, 8));
    await S.refresh();
  });
}

async function safeRun(fn) {
  try {
    await fn();
  } catch (e) {
    toast(e.message || "操作失败", "error");
  }
}

// ---------------------------------------------------------------- 导入

let browsePath = null;
let browseSelected = null;

function ensureModal() {
  let modal = $("modal");
  if (modal) return modal;
  modal = el("div", { class: "modal", id: "modal" });
  modal.appendChild(el("div", { class: "modal-box" }, [
    el("div", { class: "modal-head" }, [
      el("span", { class: "modal-title" }, "导入媒体"),
      el("button", { class: "modal-close", onclick: closeModal }, "\u00D7"),
    ]),
    el("div", { class: "modal-path", id: "modal-path" }),
    el("div", { class: "modal-list", id: "modal-list" }),
    el("div", { class: "modal-foot" }, [
      el("span", { class: "modal-info", id: "modal-info" }, "选择白名单目录中的媒体文件"),
      el("span", { class: "modal-spacer" }),
      el("button", { class: "btn", onclick: closeModal }, "取消"),
      el("button", { class: "btn primary", id: "modal-import" }, "导入"),
    ]),
  ]));
  document.body.appendChild(modal);
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) closeModal();
  });
  $("modal-import").addEventListener("click", doImport);
  return modal;
}

function closeModal() {
  const modal = $("modal");
  if (modal) modal.classList.remove("open");
  browseSelected = null;
}

function openImport() {
  const modal = ensureModal();
  modal.classList.add("open");
  browseSelected = null;
  showBrowse(null);
}

async function showBrowse(path) {
  const list = $("modal-list");
  const pathBar = $("modal-path");
  clear(list);
  clear(pathBar);
  try {
    const d = await api.browse(path);
    browsePath = d.path || null;

    if (d.parent) {
      pathBar.appendChild(el("button", { class: "chip", onclick: () => showBrowse(d.parent) }, "\u2190 上级"));
    }
    const roots = d.roots || [];
    if (!d.path && roots.length) {
      for (const r of roots) {
        pathBar.appendChild(el("button", { class: "chip", onclick: () => showBrowse(r) }, r));
      }
    } else {
      pathBar.appendChild(el("span", { class: "path-label" }, d.path || "白名单根目录"));
    }

    for (const it of d.items || []) {
      const row = el("div", {
        class: "modal-row " + (it.type === "dir" ? "dir" : "file"),
        onclick: () => {
          if (it.type === "dir") showBrowse(it.path);
          else selectBrowseFile(it, row);
        },
        ondblclick: () => {
          if (it.type === "file") {
            selectBrowseFile(it, row);
            doImport();
          }
        },
      }, [
        el("span", { class: "modal-icon" }, it.type === "dir" ? "\uD83D\uDCC1" : kindIcon(it.kind)),
        el("span", { class: "modal-name" }, it.name),
        el("span", { class: "modal-kind" }, it.kind || ""),
      ]);
      row.dataset.path = it.path;
      list.appendChild(row);
    }
    if (!(d.items || []).length) {
      list.appendChild(el("div", { class: "modal-empty" }, "此目录没有可导入的媒体"));
    }
  } catch (e) {
    list.appendChild(el("div", { class: "modal-empty error" }, e.message));
  }
}

function kindIcon(kind) {
  if (kind === "audio") return "\uD83C\uDFB5";
  if (kind === "image") return "\uD83D\uDDBC";
  return "\uD83C\uDFAC";
}

function selectBrowseFile(item, row) {
  browseSelected = item;
  pendingPaths = [item.path];
  document.querySelectorAll(".modal-row.selected").forEach((n) => n.classList.remove("selected"));
  row.classList.add("selected");
  $("modal-info").textContent = item.path;
  api.probe(item.path).then((info) => {
    const bits = [];
    if (info.duration) bits.push(info.duration.toFixed(2) + "s");
    if (info.width && info.height) bits.push(info.width + "x" + info.height);
    if (info.fps) bits.push(info.fps.toFixed(2) + "fps");
    if (info.has_audio) bits.push("含音频");
    $("modal-info").textContent = item.name + "  ·  " + bits.join("  ·  ");
  }).catch(() => {});
}

// 导入 = 进素材箱（不再直接上时间轴）
let pendingPaths = [];

async function doImport() {
  const paths = pendingPaths.length ? pendingPaths.slice() : (browseSelected ? [browseSelected.path] : []);
  if (!paths.length) {
    toast("先选择一个文件", "info");
    return;
  }
  closeModal();
  pendingPaths = [];
  await safeRun(async () => {
    const added = await assetsUi.importPaths(paths);
    if (!added.length) {
      toast("导入失败", "error");
      return;
    }
    toast("已加入素材箱：" + added.length + " 个", "ok");
  });
}

// ---------------------------------------------------------------- 提示

let toastTimer = null;
function toast(msg, kind = "info") {
  let t = $("toast");
  if (!t) {
    t = el("div", { id: "toast", class: "toast" });
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.className = "toast " + kind;
  }, 2600);
}

// ---------------------------------------------------------------- 收尾

window.addEventListener("beforeunload", () => {
  disconnect();
});

boot();


// __EOF__