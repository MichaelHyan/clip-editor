// 时间轴：轨道 / 片段 / 关键帧 / 标记 / 波形 的渲染与交互
// 结构：
//   #timeline
//     .tl-toolbar
//     .tl-main
//       .tl-heads   (固定宽轨道头列)
//       .tl-scroll  (横向滚动)
//         .tl-canvas (宽 = 时间跨度 * pxPerSec)
//           .tl-ruler
//           .tl-lane (每轨道一条, 内含 .tl-clip)
//           .tl-playhead
// 所有写操作都经 api 落盘后回填 state，不在此维护第二份真相。

import {
  state, bus, tracks, clipsOfTrack, clip, clipStart, clipDuration, clipEnd,
  selectClip, setPlayhead, setPlaying, zoomBy, fps, applyClip, applyProject, refresh,
} from "./state.js";
import { api } from "./api.js";
import { el, clear, clamp, timecode, throttle } from "./util.js";
import { evalTrack } from "./keyframe.js";
import * as assetsUi from "./assets_ui.js";
import * as subtitles from "./subtitles.js";

const HEAD_W = 196;
const RULER_H = 26;
const MIN_SPAN = 30;
const EDGE_PX = 8;
const STEP_CANDIDATES = [0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800];

let mRoot = null;
let mHeads = null;
let mScroll = null;
let mCanvas = null;
let mRuler = null;
let playheadEl = null;
let drag = null;
let headDrag = null;
let headResize = null;
let scrubbing = false;
let hoverLaneEl = null;
let suppressRender = false;

const waveCache = new Map();

const pps = () => state.view.pxPerSec || 80;
const t2x = (t) => t * pps();
const x2t = (x) => x / pps();

function span() {
  return Math.max(MIN_SPAN, state.totalDuration + 10, state.playhead + 5);
}

function laneTotalHeight() {
  let h = 0;
  for (const t of tracks()) h += t.height || 64;
  return h;
}

// ---------------------------------------------------------------- 波形缓存

function ensureWave(cid) {
  if (waveCache.has(cid)) return waveCache.get(cid);
  const c = clip(cid);
  if (!c) return null;
  const path = c.media && c.media.path;
  const probe = (c.media && c.media.probe) || {};
  const kind = (c.media && c.media.kind) || "video";
  if (!path || (kind !== "audio" && !probe.has_audio)) {
    waveCache.set(cid, null);
    return null;
  }
  waveCache.set(cid, "loading");
  api
    .waveform(path, 800)
    .then((d) => {
      waveCache.set(cid, d.peaks || []);
      if (!suppressRender) render();
    })
    .catch(() => waveCache.set(cid, null));
  return "loading";
}

// 按片段的 [srcIn, srcOut] 区间裁剪波形。
// peaks 覆盖整条源的 [0, srcDur]，只取对应索引区间绘制。
function drawWave(cv, peaks, srcIn, srcOut, srcDur) {
  const g = cv.getContext("2d");
  const w = cv.width;
  const h = cv.height;
  g.clearRect(0, 0, w, h);
  g.fillStyle = "rgba(126,196,255,0.55)";

  const n = peaks.length || 1;
  const total = Number(srcDur) > 0 ? Number(srcDur) : n;
  const a = clamp(Number(srcIn) || 0, 0, total);
  const b = clamp(Number(srcOut) != null ? Number(srcOut) : total, a, total);
  const fromIdx = (a / total) * n;
  const toIdx = (b / total) * n;
  const span = Math.max(1e-6, toIdx - fromIdx);

  for (let i = 0; i < w; i++) {
    const pi = Math.min(n - 1, Math.floor(fromIdx + (i / Math.max(1, w)) * span));
    const ph = Math.max(1, (peaks[pi] || 0) * h * 0.92);
    g.fillRect(i, (h - ph) / 2, 1, ph);
  }
}

// ---------------------------------------------------------------- 关键帧聚合

function collectKeyframeTimes(c) {
  const set = new Set();
  const visit = (obj, depth) => {
    if (!obj || typeof obj !== "object" || depth > 4) return;
    if (Array.isArray(obj.keys)) {
      for (const k of obj.keys) set.add(Math.max(0, +k.t || 0));
      return;
    }
    if (obj.static !== undefined) return;
    for (const v of Object.values(obj)) visit(v, depth + 1);
  };
  visit(c.video, 0);
  visit(c.audio, 0);
  for (const fx of c.effects || []) visit(fx.params, 0);
  return Array.from(set).sort((a, b) => a - b);
}

// ---------------------------------------------------------------- 吸附

function snapTime(t) {
  t = Math.max(0, t);
  if (state.view.snapToFrames) {
    const f = fps();
    t = Math.round(t * f) / f;
  }
  if (state.view.snapToMarkers) {
    const pts = [];
    for (const m of state.project.markers || []) pts.push(m.t);
    for (const cid of Object.keys(state.clips)) {
      pts.push(clipStart(cid));
      pts.push(clipEnd(cid));
    }
    const tol = EDGE_PX / pps();
    let best = null;
    let bd = tol;
    for (const p of pts) {
      const d = Math.abs(p - t);
      if (d < bd) {
        bd = d;
        best = p;
      }
    }
    if (best != null) t = best;
  }
  return Math.max(0, t);
}

// ---------------------------------------------------------------- 构建骨架

export function init() {
  mRoot = document.getElementById("timeline");
  if (!mRoot) {
    console.warn("[timeline] #timeline 不存在");
    return;
  }
  clear(mRoot);

  const toolbar = el("div", { class: "tl-toolbar" }, [
    el("button", { class: "tl-btn", title: "缩小 (Ctrl+滚轮)", onclick: () => zoomBy(1 / 1.4) }, "\u2212"),
    el("button", { class: "tl-btn", title: "放大", onclick: () => zoomBy(1.4) }, "+"),
    el("button", { class: "tl-btn wide", title: "适应窗口", onclick: fitToWindow }, "适应"),
    el("span", { class: "tl-sep" }),
    el("button", {
      class: "tl-btn wide", title: "添加一条视频轨（置于最上层）",
      onclick: () => addTrack("video"),
    }, "+ 视频轨"),
    el("button", {
      class: "tl-btn wide", title: "添加一条音频轨（置于最下层）",
      onclick: () => addTrack("audio"),
    }, "+ 音频轨"),
    el("span", { class: "tl-spacer" }),
    el("span", { class: "tl-hint" }, "空格 播放/暂停 · S 切分 · ←/→ 逐帧 · Del 删除片段 · Ctrl+Z/Y 撤销重做"),
  ]);

  mHeads = el("div", { class: "tl-heads", style: { width: HEAD_W + "px" } });
  mScroll = el("div", { class: "tl-scroll" });
  mCanvas = el("div", { class: "tl-canvas" });
  mRuler = el("div", { class: "tl-ruler", style: { height: RULER_H + "px" } });
  playheadEl = el("div", { class: "tl-playhead" });

  mCanvas.appendChild(mRuler);
  mCanvas.appendChild(playheadEl);
  mScroll.appendChild(mCanvas);

  const main = el("div", { class: "tl-main" }, [mHeads, mScroll]);
  mRoot.appendChild(toolbar);
  mRoot.appendChild(main);

  mRuler.addEventListener("pointerdown", onRulerDown);
  mScroll.addEventListener("scroll", () => {
    state.view.scrollLeft = mScroll.scrollLeft;
    syncHeadsScroll();
  });
  mScroll.addEventListener("wheel", onWheel, { passive: false });

  bus.on("timeline:changed", throttle(() => {
    if (drag || subDrag) return;
    render();
  }, 60));
  bus.on("subtitle:changed", () => { if (!drag && !subDrag) render(); });
  bus.on("selection:changed", throttle(() => {
    if (drag) return;
    render();
  }, 40));
  bus.on("zoom:changed", () => render());
  bus.on("project:loaded", () => render());
  bus.on("project:changed", () => render());
  bus.on("playhead:changed", () => positionPlayhead());
  bus.on("timeline:reveal", (cid) => revealClip(cid));

  render();
}

// ---------------------------------------------------------------- 渲染

function render() {
  if (!mCanvas || suppressRender) return;
  if (drag || headDrag || headResize) return;   // 拖拽期间禁止重建节点，否则节点变游离
  const sp = span();
  const width = Math.max(400, t2x(sp));
  mCanvas.style.width = width + "px";
  renderRuler(sp, width);
  renderHeads();
  renderLanes();
  positionPlayhead();
}

function chooseStep() {
  const p = pps();
  for (const s of STEP_CANDIDATES) {
    if (s * p >= 72) return s;
  }
  return STEP_CANDIDATES[STEP_CANDIDATES.length - 1];
}

function renderRuler(sp, width) {
  clear(mRuler);
  const step = chooseStep();
  const minor = step / 5;
  const count = Math.ceil(sp / minor) + 2;
  for (let i = 0; i < count; i++) {
    const t = i * minor;
    const x = t2x(t);
    if (x > width + 40) break;
    const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
    const tick = el("div", {
      class: "tl-tick" + (major ? " major" : ""),
      style: { left: x + "px" },
    });
    if (major) {
      tick.appendChild(el("span", { class: "tl-tick-label" }, timecode(t).slice(3, 11)));
    }
    mRuler.appendChild(tick);
  }
}

function renderHeads() {
  clear(mHeads);
  // 内层容器承载所有行，通过 transform 跟随垂直滚动
  const inner = el("div", { class: "tl-heads-inner" });
  inner.appendChild(el("div", { class: "tl-head-spacer", style: { height: RULER_H + "px" } }));
  for (const tr of tracks()) {
    const nameEl = el("div", {
      class: "tl-head-name",
      title: "双击重命名 · 按住拖动可调整轨道顺序",
    }, tr.name || tr.type.toUpperCase());
    nameEl.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      startRenameTrack(tr, nameEl);
    });

    const btns = tr.type === "subtitle"
      ? [headBtn("+", "在播放头处加字幕", false, addSubtitleHere)]
      : [
          headBtn("M", "静音", tr.muted, () => toggleTrack(tr.id, "muted")),
          headBtn("H", "隐藏", tr.hidden, () => toggleTrack(tr.id, "hidden")),
          headBtn("L", "锁定", tr.locked, () => toggleTrack(tr.id, "locked")),
          // 类型切换只对空轨道可用，且仅 video <-> audio
          headBtn("\u21C4", "切换类型（视频/音频，仅空轨道）", false,
                  () => changeTrackType(tr)),
          // 字幕轨不提供删除：normalize() 会重新补一条，删了只会让人困惑
          deleteTrackBtn(tr),
        ];

    const row = el("div", {
      class: "tl-head type-" + tr.type,
      dataset: { track: tr.id },
      style: { height: (tr.height || 64) + "px" },
    }, [
      nameEl,
      el("div", { class: "tl-head-btns" }, btns),
    ]);

    // 下边缘把手：拖动调整轨道高度
    const rz = el("div", {
      class: "tl-head-resize",
      title: "拖动调整轨道高度（双击恢复默认 64px）",
    });
    rz.addEventListener("pointerdown", (ev) => onHeadResizeDown(ev, tr, row));
    rz.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      patchTrackProps(tr.id, { height: 64 }).then(() => render());
    });
    row.appendChild(rz);

    // 整行作为拖动排序的手柄；按钮/输入框在 onHeadDown 里排除
    row.addEventListener("pointerdown", (ev) => onHeadDown(ev, tr));
    inner.appendChild(row);
  }
  mHeads.appendChild(inner);
  syncHeadsScroll();
}

async function addSubtitleHere() {
  if (state.playing) setPlaying(false);
  const sub = await subtitles.addSubtitleAtPlayhead("新字幕");
  if (sub) {
    subtitles.selectSubtitle(sub.id);
    bus.emit("toast", { msg: "已添加字幕，在右侧编辑内容", kind: "ok" });
    render();
  }
}

function headBtn(text, title, active, onclick) {
  return el("button", {
    class: "tl-head-btn" + (active ? " active" : ""),
    title,
    onclick: (ev) => {
      ev.stopPropagation();
      onclick();
    },
  }, text);
}

function deleteTrackBtn(tr) {
  return el("button", {
    class: "tl-head-btn danger",
    title: "删除轨道「" + (tr.name || tr.type) + "」",
    onclick: (ev) => {
      ev.stopPropagation();
      deleteTrack(tr);
    },
  }, "\u00D7");
}

// ---------------------------------------------------------------- 轨道增删

// 命名：取同类型轨道名末尾数字的最大值 +1（V2/V1 -> V3，A1 -> A2）
function nextTrackName(type) {
  let maxNum = 0;
  for (const t of tracks()) {
    if (t.type !== type) continue;
    const m = String(t.name || "").match(/(\d+)\s*$/);
    if (m) maxNum = Math.max(maxNum, parseInt(m[1], 10));
  }
  return (type === "audio" ? "A" : "V") + (maxNum + 1);
}

async function addTrack(type) {
  if (!state.pid) return;
  const name = nextTrackName(type);
  // 视频轨置顶必须传负数：后端 append 后按 index 稳定排序，
  // 传 0 会被原有 index=0 的轨道压在后面。音频轨传大数落到底部。
  const index = type === "video" ? -1 : 9999;
  try {
    const res = await fetch(`/api/projects/${state.pid}/tracks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, name, index }),
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.error || "添加失败");
    applyProject(d.data);
    bus.emit("toast", { msg: "已添加轨道 " + name, kind: "ok" });
  } catch (e) {
    console.error("[timeline] 添加轨道失败", e);
    bus.emit("toast", { msg: "添加轨道失败：" + e.message, kind: "error" });
  }
}

async function deleteTrack(tr) {
  if (!state.pid || !tr) return;
  const n = (tr.clips || []).length;
  const msg = n
    ? `删除轨道「${tr.name || tr.type}」？\n\n该轨道上的 ${n} 个片段会一并删除（源文件不受影响）。`
    : `删除轨道「${tr.name || tr.type}」？`;
  if (!confirm(msg)) return;
  try {
    const res = await fetch(`/api/projects/${state.pid}/tracks/${tr.id}`, {
      method: "DELETE",
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.error || "删除失败");
    await refresh();
    // 被删轨道上的片段已不存在，清掉指向它的选中状态
    if (state.selectedClipId && !state.clips[state.selectedClipId]) {
      selectClip(null);
    }
    bus.emit("toast", { msg: "已删除轨道 " + (tr.name || tr.type), kind: "ok" });
  } catch (e) {
    console.error("[timeline] 删除轨道失败", e);
    bus.emit("toast", { msg: "删除轨道失败：" + e.message, kind: "error" });
  }
}

// ---------------------------------------------------------------- 轨道重命名

function startRenameTrack(tr, nameEl) {
  const input = el("input", {
    class: "tl-head-name-input",
    value: tr.name || tr.type,
  });
  nameEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== tr.name) {
      await patchTrackProps(tr.id, { name: next });
    } else {
      render();
    }
  };

  // 阻止冒泡：否则 pointerdown 会启动轨道拖动
  input.addEventListener("pointerdown", (ev) => ev.stopPropagation());
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") { ev.preventDefault(); commit(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); commit(false); }
  });
  input.addEventListener("blur", () => commit(true));
}

async function patchTrackProps(tid, patch) {
  if (!state.pid) return null;
  try {
    const pj = await api.patchTrack(state.pid, tid, patch);
    applyProject(pj);
    return pj;
  } catch (e) {
    console.error("[timeline] 修改轨道失败", e);
    bus.emit("toast", { msg: "修改轨道失败：" + e.message, kind: "error" });
    render();
    return null;
  }
}

// ---------------------------------------------------------------- 轨道拖动排序

function onHeadDown(ev, tr) {
  if (ev.button !== 0) return;
  // 按钮和重命名输入框不触发排序
  if (ev.target.closest("button") || ev.target.closest("input")) return;

  const rows = Array.from(mHeads.querySelectorAll(".tl-head")).map((n) => {
    const r = n.getBoundingClientRect();
    return { id: n.dataset.track, top: r.top, h: r.height, mid: (r.top + r.bottom) / 2 };
  });
  if (rows.length < 2) return;

  headDrag = {
    tid: tr.id,
    row: ev.currentTarget,
    startY: ev.clientY,
    moved: false,
    rows,
    targetIdx: -1,
  };
  window.addEventListener("pointermove", onHeadMove);
  window.addEventListener("pointerup", onHeadUp);
}

function onHeadMove(ev) {
  if (!headDrag) return;
  if (!headDrag.moved && Math.abs(ev.clientY - headDrag.startY) < 4) return;
  headDrag.moved = true;
  headDrag.row.classList.add("dragging-head");

  // 插入位置：第一个 mid 大于指针 Y 的行之前
  let idx = headDrag.rows.length;
  for (let i = 0; i < headDrag.rows.length; i++) {
    if (ev.clientY < headDrag.rows[i].mid) { idx = i; break; }
  }
  headDrag.targetIdx = idx;
  showInsertLine(idx);
}

function showInsertLine(idx) {
  const inner = mHeads.querySelector(".tl-heads-inner");
  if (!inner || !headDrag) return;
  let line = inner.querySelector(".tl-head-insert");
  if (!line) {
    line = el("div", { class: "tl-head-insert" });
    inner.appendChild(line);
  }
  const rows = headDrag.rows;
  const y = idx <= 0
    ? rows[0].top
    : (idx >= rows.length ? rows[rows.length - 1].top + rows[rows.length - 1].h : rows[idx].top);
  const ir = inner.getBoundingClientRect();
  line.style.top = Math.round(y - ir.top) + "px";
  line.style.display = "block";
}

async function onHeadUp() {
  window.removeEventListener("pointermove", onHeadMove);
  window.removeEventListener("pointerup", onHeadUp);
  if (!headDrag) return;
  const d = headDrag;
  headDrag = null;

  if (d.row && d.row.isConnected) d.row.classList.remove("dragging-head");
  const inner = mHeads && mHeads.querySelector(".tl-heads-inner");
  if (inner) {
    const line = inner.querySelector(".tl-head-insert");
    if (line) line.remove();
  }
  if (!d.moved || d.targetIdx < 0) return;

  const order = tracks().map((t) => t.id);
  const from = order.indexOf(d.tid);
  if (from < 0) return;
  // 插入线给的是「插到第 idx 位之前」，移除自身后若目标在其后要左移一位
  let to = d.targetIdx;
  if (to > from) to -= 1;
  if (to === from) return;

  order.splice(from, 1);
  order.splice(to, 0, d.tid);

  try {
    const pj = await api.reorderTracks(state.pid, order);
    applyProject(pj);
  } catch (e) {
    console.error("[timeline] 轨道排序失败", e);
    bus.emit("toast", { msg: "轨道排序失败：" + e.message, kind: "error" });
    render();
  }
}

// ---------------------------------------------------------------- 轨道高度

const MIN_TRACK_H = 28;
const MAX_TRACK_H = 240;

function onHeadResizeDown(ev, tr, row) {
  if (ev.button !== 0) return;
  ev.preventDefault();
  ev.stopPropagation();      // 不要触发整行的拖动排序
  const lane = mCanvas
    ? mCanvas.querySelector('.tl-lane[data-track="' + tr.id + '"]')
    : null;
  const h0 = row.getBoundingClientRect().height;
  headResize = {
    tid: tr.id, tr, row, lane,
    startY: ev.clientY, origH: h0, h: h0, moved: false,
  };
  row.classList.add("resizing-head");
  window.addEventListener("pointermove", onHeadResizeMove);
  window.addEventListener("pointerup", onHeadResizeUp);
}

function onHeadResizeMove(ev) {
  if (!headResize) return;
  const dy = ev.clientY - headResize.startY;
  if (!headResize.moved && Math.abs(dy) < 2) return;
  headResize.moved = true;
  const h = clamp(Math.round(headResize.origH + dy), MIN_TRACK_H, MAX_TRACK_H);
  headResize.h = h;
  // 直接改 DOM，不重建节点（render 在拖动期间被守卫拦住）
  headResize.row.style.height = h + "px";
  if (headResize.lane) headResize.lane.style.height = h + "px";
  // 乐观写本地高度：positionPlayhead() 用 laneTotalHeight() 读 tracks()
  if (headResize.tr) headResize.tr.height = h;
  positionPlayhead();
  syncHeadsScroll();
}

async function onHeadResizeUp() {
  window.removeEventListener("pointermove", onHeadResizeMove);
  window.removeEventListener("pointerup", onHeadResizeUp);
  if (!headResize) return;
  const d = headResize;
  headResize = null;
  if (d.row && d.row.isConnected) d.row.classList.remove("resizing-head");
  if (!d.moved) {
    // 没移动就什么都没变。这里绝不能 render()：
    // 双击 = down/up + down/up + dblclick，第一次 up 就重建节点会让
    // 后续 dblclick 在已游离的旧节点上触发，监听器已消失 -> 双击失效。
    return;
  }
  await patchTrackProps(d.tid, { height: d.h });
  render();
}

// ---------------------------------------------------------------- 类型切换

async function changeTrackType(tr) {
  if (!state.pid || !tr) return;
  if (tr.type === "subtitle") {
    bus.emit("toast", { msg: "字幕轨类型固定，不能切换", kind: "info" });
    return;
  }
  if (tr.type !== "video" && tr.type !== "audio") {
    bus.emit("toast", { msg: "该轨道类型不支持切换", kind: "info" });
    return;
  }
  const n = (tr.clips || []).length;
  if (n) {
    bus.emit("toast", {
      msg: `轨道上还有 ${n} 个片段，请先清空再改类型`,
      kind: "error",
    });
    return;
  }
  const next = tr.type === "video" ? "audio" : "video";
  const nextLabel = next === "video" ? "视频" : "音频";
  const curLabel = tr.type === "video" ? "视频" : "音频";
  const newName = nextTrackName(next);
  if (!confirm(
    `把轨道「${tr.name || tr.type}」从 ${curLabel} 改为 ${nextLabel}？\n\n` +
    `轨道会重命名为「${newName}」。\n仅空轨道可以改类型。`
  )) return;
  await patchTrackProps(tr.id, { type: next, name: newName });
}

function renderLanes() {
  Array.from(mCanvas.querySelectorAll(".tl-lane")).forEach((n) => n.remove());
  const list = tracks();
  for (const tr of list) {
    const lane = el("div", {
      class: "tl-lane type-" + tr.type,
      dataset: { track: tr.id, type: tr.type },
      style: { height: (tr.height || 64) + "px" },
    });
    lane.addEventListener("pointerdown", onLaneDown);
    lane.addEventListener("dragover", onLaneDragOver);
    lane.addEventListener("dragleave", onLaneDragLeave);
    lane.addEventListener("drop", onLaneDrop);
    if (tr.type === "subtitle") {
      for (const sub of subtitles.listSubtitles()) {
        lane.appendChild(renderSubtitleEl(sub));
      }
    } else {
      for (const c of clipsOfTrack(tr.id)) lane.appendChild(renderClipEl(c));
    }
    mCanvas.appendChild(lane);
  }
  // 保证播放头始终在最上层
  if (playheadEl) mCanvas.appendChild(playheadEl);
}

// 字幕块：可点击选中、可拖动改时间、可拖两边改时长
function renderSubtitleEl(sub) {
  const left = t2x(sub.start);
  const w = Math.max(6, t2x(sub.end - sub.start));
  const node = el("div", {
    class: "tl-sub",
    dataset: { sid: sub.id },
    style: { left: left + "px", width: w + "px" },
    title: (sub.text || "").slice(0, 60) + "\n" + sub.start.toFixed(2) + "s ~ " + sub.end.toFixed(2) + "s",
  });
  if (sub.id === state.selectedSubtitleId) node.classList.add("selected");
  node.appendChild(el("div", { class: "tl-sub-edge left" }));
  node.appendChild(el("div", { class: "tl-sub-edge right" }));
  node.appendChild(el("div", { class: "tl-sub-label" }, sub.text || "（空）"));
  node.addEventListener("pointerdown", onSubtitleDown);
  node.addEventListener("dblclick", (ev) => {
    ev.stopPropagation();
    subtitles.selectSubtitle(sub.id);
    bus.emit("subtitle:focus", sub.id);
  });
  return node;
}

// 字幕拖拽：左右边缘改时长，中间改起点
let subDrag = null;

function onSubtitleDown(ev) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  const node = ev.currentTarget;
  const sid = node.dataset.sid;
  const sub = subtitles.listSubtitles().find((x) => x.id === sid);
  if (!sub) return;
  if (state.playing) setPlaying(false);
  subtitles.selectSubtitle(sid);

  const rect = node.getBoundingClientRect();
  const localX = ev.clientX - rect.left;
  let mode = "move";
  if (localX < 6) mode = "trim-l";
  else if (localX > rect.width - 6) mode = "trim-r";

  subDrag = {
    mode, sid, node,
    startX: ev.clientX,
    origStart: sub.start,
    origEnd: sub.end,
    moved: false,
    newStart: sub.start,
    newEnd: sub.end,
  };
  node.classList.add("dragging-sub");
  window.addEventListener("pointermove", onSubtitleMove);
  window.addEventListener("pointerup", onSubtitleUp);
}

function onSubtitleMove(ev) {
  if (!subDrag) return;
  const dx = ev.clientX - subDrag.startX;
  if (Math.abs(dx) > 2) subDrag.moved = true;
  const dt = dx / pps();
  const node = subDrag.node;

  if (subDrag.mode === "move") {
    const dur = subDrag.origEnd - subDrag.origStart;
    let ns = Math.max(0, snapTime(subDrag.origStart + dt));
    subDrag.newStart = ns;
    subDrag.newEnd = ns + dur;
    node.style.left = t2x(ns) + "px";
    node.style.width = Math.max(6, t2x(dur)) + "px";
    return;
  }
  if (subDrag.mode === "trim-l") {
    let ns = clamp(snapTime(subDrag.origStart + dt), 0, subDrag.origEnd - 0.1);
    subDrag.newStart = ns;
    subDrag.newEnd = subDrag.origEnd;
    node.style.left = t2x(ns) + "px";
    node.style.width = Math.max(6, t2x(subDrag.origEnd - ns)) + "px";
    return;
  }
  if (subDrag.mode === "trim-r") {
    let ne = snapTime(subDrag.origEnd + dt);
    if (ne < subDrag.origStart + 0.1) ne = subDrag.origStart + 0.1;
    subDrag.newEnd = ne;
    node.style.width = Math.max(6, t2x(ne - subDrag.origStart)) + "px";
  }
}

async function onSubtitleUp() {
  window.removeEventListener("pointermove", onSubtitleMove);
  window.removeEventListener("pointerup", onSubtitleUp);
  if (!subDrag) return;
  const d = subDrag;
  subDrag = null;
  if (d.node) d.node.classList.remove("dragging-sub");
  if (!d.moved) return;
  const patch = {};
  if (Math.abs(d.newStart - d.origStart) > 1e-4) patch.start = round3(d.newStart);
  if (Math.abs(d.newEnd - d.origEnd) > 1e-4) patch.end = round3(d.newEnd);
  if (Object.keys(patch).length) {
    await subtitles.patchSubtitle(d.sid, patch);
  }
  render();
}

function round3(v) {
  return Math.round(v * 1000) / 1000;
}

function renderClipEl(c) {
  const left = t2x(clipStart(c.id));
  const w = Math.max(3, t2x(clipDuration(c.id)));
  const node = el("div", {
    class: "tl-clip",
    dataset: { cid: c.id },
    style: { left: left + "px", width: w + "px" },
  });
  if (c.id === state.selectedClipId) node.classList.add("selected");
  const kind = (c.media && c.media.kind) || "video";
  node.classList.add("kind-" + kind);

  node.appendChild(el("div", { class: "tl-clip-edge left" }));
  node.appendChild(el("div", { class: "tl-clip-edge right" }));
  node.appendChild(el("div", { class: "tl-clip-label" }, c.name || c.id));

  // 只有音频片段画波形；视频片段不画（视频的时间轴上画波形没有意义）
  if (kind === "audio") {
    const peaks = ensureWave(c.id);
    if (Array.isArray(peaks) && peaks.length) {
      const cv = el("canvas", { class: "tl-wave" });
      cv.width = Math.max(2, Math.round(w));
      cv.height = 36;
      // 按片段的剪辑区间裁剪波形
      const sec = c.audio || {};
      const srcDur = Number((c.media && c.media.probe && c.media.probe.duration)) || 0;
      const srcIn = evalTrack(sec.src_in, 0, 0) || 0;
      let srcOut = evalTrack(sec.src_out, 0, null);
      if (srcOut == null) srcOut = srcDur;
      drawWave(cv, peaks, srcIn, srcOut, srcDur);
      node.appendChild(cv);
    } else {
      node.appendChild(el("div", { class: "tl-wave-pending" }));
    }
  }

  for (const t of collectKeyframeTimes(c)) {
    node.appendChild(el("div", {
      class: "tl-kf",
      style: { left: clamp(t2x(t), 2, Math.max(2, w - 2)) + "px" },
      title: "关键帧 " + t.toFixed(2) + "s",
    }));
  }

  for (const m of c.markers || []) {
    node.appendChild(el("div", {
      class: "tl-clip-marker",
      style: { left: clamp(t2x(m.t || 0), 0, w) + "px", background: m.color || "#ffcc00" },
      title: m.label || "标记",
    }));
  }

  node.addEventListener("pointerdown", onClipDown);
  node.addEventListener("dblclick", (ev) => {
    ev.stopPropagation();
    selectClip(c.id, { force: true });
  });
  return node;
}

function positionPlayhead() {
  if (!playheadEl) return;
  playheadEl.style.left = t2x(state.playhead) + "px";
  playheadEl.style.height = RULER_H + laneTotalHeight() + "px";
}

// ---------------------------------------------------------------- 轨道属性

async function toggleTrack(tid, field) {
  const tr = tracks().find((t) => t.id === tid);
  if (!tr) return;
  const next = !tr[field];
  try {
    const res = await fetch(`/api/projects/${state.pid}/tracks/${tid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch: { [field]: next } }),
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.error || "轨道更新失败");
    applyProject(d.data);
  } catch (e) {
    console.error("[timeline] 轨道属性失败", e);
  }
}

// ---------------------------------------------------------------- 缩放 / 滚动

function onWheel(ev) {
  if (ev.ctrlKey || ev.metaKey) {
    ev.preventDefault();
    const rect = mScroll.getBoundingClientRect();
    const anchorX = ev.clientX - rect.left + mScroll.scrollLeft;
    const anchorSec = x2t(anchorX);
    zoomBy(ev.deltaY < 0 ? 1.12 : 1 / 1.12);
    // 缩放后保持锚点位置
    requestAnimationFrame(() => {
      mScroll.scrollLeft = t2x(anchorSec) - (ev.clientX - rect.left);
      state.view.scrollLeft = mScroll.scrollLeft;
    });
  }
}

function fitToWindow() {
  if (!mScroll) return;
  const avail = mScroll.clientWidth - 24;
  const total = Math.max(1, state.totalDuration || MIN_SPAN);
  state.view.pxPerSec = clamp(avail / total, 4, 2000);
  bus.emit("zoom:changed", state.view.pxPerSec);
  mScroll.scrollLeft = 0;
  render();
}

export function revealClip(cid) {
  if (!mScroll || !cid) return;
  const s = clipStart(cid);
  const e = clipEnd(cid);
  const left = t2x(s);
  const right = t2x(e);
  const viewLeft = mScroll.scrollLeft;
  const viewRight = viewLeft + mScroll.clientWidth;
  if (left < viewLeft + 40) mScroll.scrollLeft = Math.max(0, left - 60);
  else if (right > viewRight - 40) mScroll.scrollLeft = Math.max(0, right - mScroll.clientWidth + 60);
  state.view.scrollLeft = mScroll.scrollLeft;
}

// ---------------------------------------------------------------- 播放头交互

function onRulerDown(ev) {
  if (ev.button !== 0) return;
  if (state.playing) setPlaying(false);
  startScrub(ev);
}

function startScrub(ev) {
  scrubbing = true;
  applyScrub(ev);
  const move = (e) => scrubbing && applyScrub(e);
  const up = () => {
    scrubbing = false;
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function applyScrub(ev) {
  const rect = mScroll.getBoundingClientRect();
  const x = ev.clientX - rect.left + mScroll.scrollLeft;
  setPlayhead(snapTime(x2t(x)));
}

// 轨道头跟随垂直滚动。
// 不能直接设 scrollTop：.tl-heads 是 overflow:hidden，内容不溢出时 scrollTop 无效。
// 改用 transform 位移内部容器，任何情况下都能对齐。
function syncHeadsScroll() {
  if (!mHeads || !mScroll) return;
  const y = mScroll.scrollTop || 0;
  const inner = mHeads.querySelector(".tl-heads-inner");
  if (inner) {
    inner.style.transform = "translateY(" + (-y) + "px)";
  }
}

// ---------------------------------------------------------------- 素材拖入

const ROLE_TO_TRACK = {
  video: ["video", "effect"],
  audio: ["audio", "video"],
  image: ["video", "effect"],
};

function laneAcceptsRole(laneType, role) {
  const ok = ROLE_TO_TRACK[role] || ["video"];
  return ok.includes(laneType);
}

function onLaneDragOver(ev) {
  const card = assetsUi.currentDragCard();
  if (!card) return;
  const lane = ev.currentTarget;
  if (!laneAcceptsRole(lane.dataset.type, card.role)) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = "copy";
  lane.classList.add("drop-target");
}

function onLaneDragLeave(ev) {
  ev.currentTarget.classList.remove("drop-target");
}

async function onLaneDrop(ev) {
  const lane = ev.currentTarget;
  lane.classList.remove("drop-target");
  const card = assetsUi.currentDragCard();
  if (!card) {
    // 兜底：从 dataTransfer 解析
    try {
      const raw = ev.dataTransfer.getData("text/plain");
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const found = assetsUi.findByAsset(parsed.asset_id, parsed.role);
      if (found) return doAssetDrop(found, lane, ev);
    } catch (e) {
      return;
    }
    return;
  }
  if (!laneAcceptsRole(lane.dataset.type, card.role)) return;
  ev.preventDefault();
  await doAssetDrop(card, lane, ev);
}

async function doAssetDrop(card, lane, ev) {
  const rect = mScroll.getBoundingClientRect();
  const x = ev.clientX - rect.left + mScroll.scrollLeft;
  const dropT = snapTime(Math.max(0, x2t(x)));
  const trackId = lane.dataset.track;
  try {
    await assetsUi.addToTimeline(card, { track_id: trackId, start: dropT });
  } catch (e) {
    console.error("[timeline] 素材落轨失败", e);
  }
}

// ---------------------------------------------------------------- 空白点击

function onLaneDown(ev) {
  if (ev.button !== 0) return;
  if (ev.target.closest(".tl-clip")) return;
  if (state.playing) setPlaying(false);
  selectClip(null);
  if (state.selectedSubtitleId) {
    state.selectedSubtitleId = null;
    bus.emit("selection:changed", null);
  }
  startScrub(ev);
}

// ---------------------------------------------------------------- 邻居边界 / 防重叠

// 同轨上位于 self 右侧最近的片段起点（没有则 Infinity）
function rightBoundary(trackId, selfId, selfStart, selfDur) {
  if (!trackId) return Infinity;
  let best = Infinity;
  const selfEnd = selfStart + selfDur;
  for (const c of clipsOfTrack(trackId)) {
    if (c.id === selfId) continue;
    const cs = clipStart(c.id);
    if (cs >= selfEnd - 1e-6 && cs < best) best = cs;
  }
  return best;
}

// 同轨上位于 self 左侧最近的片段终点（没有则 0）
function leftBoundary(trackId, selfId, selfStart) {
  if (!trackId) return 0;
  let best = 0;
  for (const c of clipsOfTrack(trackId)) {
    if (c.id === selfId) continue;
    const ce = clipStart(c.id) + clipDuration(c.id);
    if (ce <= selfStart + 1e-6 && ce > best) best = ce;
  }
  return best;
}

// 把期望起点吸附到该轨上不与任何片段重叠的最近位置
function findFreeStart(trackId, selfId, desiredStart, dur) {
  if (!trackId) return Math.max(0, desiredStart);
  const others = clipsOfTrack(trackId)
    .filter((c) => c.id !== selfId)
    .map((c) => [clipStart(c.id), clipStart(c.id) + clipDuration(c.id)])
    .sort((a, b) => a[0] - b[0]);
  let s = Math.max(0, desiredStart);
  for (let iter = 0; iter < 6; iter++) {
    const end = s + dur;
    let hit = null;
    for (const o of others) {
      if (s < o[1] - 1e-6 && end > o[0] + 1e-6) {
        hit = o;
        break;
      }
    }
    if (!hit) break;
    const toLeft = Math.max(0, hit[0] - dur);
    const toRight = hit[1];
    s = Math.abs(s - toLeft) <= Math.abs(s - toRight) ? toLeft : toRight;
  }
  return Math.max(0, s);
}

// ---------------------------------------------------------------- 片段拖拽

// 目标轨道高亮：拖拽时给候选 lane 加类，松手或离开时清掉
// 用鼠标 Y 坐标找目标 lane（不依赖 elementFromPoint，更可靠）
function laneAtY(clientY, wantType) {
  if (!mCanvas) return null;
  const lanes = Array.from(mCanvas.querySelectorAll(".tl-lane"));
  const sameType = lanes.filter((l) => l.dataset.type === wantType);
  if (!sameType.length) return null;

  // 优先：鼠标 Y 落在某条 lane 内
  for (const l of sameType) {
    const r = l.getBoundingClientRect();
    if (clientY >= r.top && clientY < r.bottom) return l;
  }

  // 兜底：Y 在 lane 缝隙里时，取最近的一条（容差 40px）
  let best = null;
  let bestD = Infinity;
  for (const l of sameType) {
    const r = l.getBoundingClientRect();
    const cy = (r.top + r.bottom) / 2;
    const d = Math.abs(clientY - cy);
    if (d < bestD) {
      bestD = d;
      best = l;
    }
  }
  return bestD <= (best ? best.getBoundingClientRect().height / 2 + 40 : 0) ? best : null;
}

// 每次现查节点：拖拽期间可能因重渲染被替换
function getDragNode(cid) {
  if (!mCanvas || !cid) return null;
  return mCanvas.querySelector('.tl-clip[data-cid="' + cid + '"]');
}

function setHoverLane(el) {
  if (hoverLaneEl === el) return;
  if (hoverLaneEl) hoverLaneEl.classList.remove("drop-target");
  hoverLaneEl = el;
  if (hoverLaneEl) hoverLaneEl.classList.add("drop-target");
}

function trackTypeOf(cid) {
  const c = clip(cid);
  const kind = (c && c.media && c.media.kind) || "video";
  return kind === "audio" ? "audio" : "video";
}

function speedOf(c) {
  const sec = (c.media && c.media.kind === "audio") ? c.audio : c.video;
  return evalTrack(sec && sec.speed, 0, 1) || 1;
}

function onClipDown(ev) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  const node = ev.currentTarget;
  const cid = node.dataset.cid;
  const c = clip(cid);
  if (!c) return;
  if (state.playing) setPlaying(false);

  const tr = tracks().find((t) => t.id === (c.timeline || {}).track_id);
  if (tr && tr.locked) return;

  const rect = node.getBoundingClientRect();
  const localX = ev.clientX - rect.left;
  let mode = "move";
  if (localX < EDGE_PX) mode = "trim-l";
  else if (localX > rect.width - EDGE_PX) mode = "trim-r";

  const sec = (c.media && c.media.kind === "audio") ? c.audio : c.video;
  const probe = (c.media && c.media.probe) || {};
  const probeDur = Number(probe.duration) || 0;
  let srcOut = evalTrack(sec && sec.src_out, 0, null);
  if (srcOut == null) srcOut = probeDur;
  // 源媒体真实结尾：右边缘能拉长的上限，不受当前 src_out 限制
  const srcMax = probeDur > 0 ? probeDur : srcOut;

  drag = {
    mode,
    cid,
    node,
    startX: ev.clientX,
    startY: ev.clientY,
    origStart: clipStart(cid),
    origDur: clipDuration(cid),
    origTrack: (c.timeline || {}).track_id,
    targetTrack: (c.timeline || {}).track_id,
    srcIn: evalTrack(sec && sec.src_in, 0, 0) || 0,
    srcOut: srcOut,
    srcMax: srcMax,
    speed: speedOf(c),
    start: clipStart(cid),
    dur: clipDuration(cid),
    moved: false,
    srcInNew: null,
    srcOutNew: null,
  };

  // 先挂上拖拽态，再触发选中（避免 selectClip 的 render 重建节点）
  node.classList.add("dragging-clip");
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", onDragUp);
  selectClip(cid);
}

function onDragMove(ev) {
  if (!drag) return;
  try {
    onDragMoveInner(ev);
  } catch (e) {
    console.error("[timeline] 拖拽异常", e);
    drag.err = String(e);
  }
}

function onDragMoveInner(ev) {
  const dx = ev.clientX - drag.startX;
  const dyMoved = Math.abs(ev.clientY - drag.startY);
  // 任意方向位移都算"动过"：跨轨拖拽常常是纯垂直的（dx=0）
  if (Math.abs(dx) > 2 || dyMoved > 2) drag.moved = true;
  const dt = dx / pps();
  // 现查节点：若被替换则重新绑定，保证位移与高亮作用于真实 DOM
  let node = drag.node;
  if (!node || !node.isConnected) {
    const fresh = getDragNode(drag.cid);
    if (fresh) {
      fresh.classList.add("dragging-clip");
      drag.node = fresh;
      node = fresh;
    }
  }

  if (drag.mode === "move") {
    let ns = snapTime(drag.origStart + dt);
    drag.start = ns;
    node.style.left = t2x(ns) + "px";

    const dy = ev.clientY - drag.startY;
    // 只要垂直移动过就检测轨道（阈值 4px 更灵敏）
    if (Math.abs(dy) > 4) {
      const wantType = trackTypeOf(drag.cid);
      const lane = laneAtY(ev.clientY, wantType);
      if (lane) {
        drag.targetTrack = lane.dataset.track;
        setHoverLane(lane);
      } else {
        // 不在任何同类型轨道上：清高亮，保留上次合法目标
        setHoverLane(null);
      }
    }
    return;
  }

  if (drag.mode === "trim-l") {
    const origEnd = drag.origStart + drag.origDur;
    // 最早可到：源开头 与 左邻居片段终点 的较大者
    const srcMinStart = drag.origStart - drag.srcIn / drag.speed;
    const lb = leftBoundary(drag.origTrack, drag.cid, drag.origStart);
    const minStart = Math.max(srcMinStart, lb);
    let ns = snapTime(drag.origStart + dt);
    ns = clamp(ns, minStart, origEnd - 0.05);
    const delta = ns - drag.origStart;
    drag.start = ns;
    drag.dur = drag.origDur - delta;
    drag.srcInNew = Math.max(0, drag.srcIn + delta * drag.speed);
    node.style.left = t2x(ns) + "px";
    node.style.width = Math.max(3, t2x(drag.dur)) + "px";
    return;
  }

  if (drag.mode === "trim-r") {
    // 上限 = 源媒体结尾 与 右邻居片段起点 的较小者
    const srcMaxEnd = drag.origStart + (drag.srcMax - drag.srcIn) / drag.speed;
    const nb = rightBoundary(drag.origTrack, drag.cid, drag.origStart, drag.origDur);
    const maxEnd = Math.min(srcMaxEnd, nb);
    let ne = snapTime(drag.origStart + drag.origDur + dt);
    ne = clamp(ne, drag.origStart + 0.05, maxEnd);
    drag.dur = ne - drag.origStart;
    drag.srcOutNew = Math.min(drag.srcMax, drag.srcIn + drag.dur * drag.speed);
    node.style.width = Math.max(3, t2x(drag.dur)) + "px";
  }
}

async function onDragUp() {
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", onDragUp);
  if (!drag) return;
  const d = drag;
  drag = null;
  const cleanupNode = (d.node && d.node.isConnected) ? d.node : getDragNode(d.cid);
  if (cleanupNode) cleanupNode.classList.remove("dragging-clip");
  setHoverLane(null);
  suppressRender = true;
  try {
    if (d.mode === "move") {
      if (!d.moved) return;
      const tgt = d.targetTrack || d.origTrack;
      // 落位时吸附到该轨上不与任何片段重叠的最近位置
      const finalStart = findFreeStart(tgt, d.cid, d.start, d.origDur);
      await commitMove(d.cid, d.targetTrack, finalStart, d.origTrack);
    } else if (d.mode === "trim-l") {
      if (!d.moved || d.srcInNew == null) return;
      // 只改 src_in，src_out 传 null 保持原样（可能仍是"到结尾"）
      await commitTrim(d.cid, d.srcInNew, null, d.start);
    } else if (d.mode === "trim-r") {
      if (!d.moved || d.srcOutNew == null) return;
      // 只改 src_out，src_in 传 null 保持原样
      await commitTrim(d.cid, null, d.srcOutNew, null);
    }
  } finally {
    suppressRender = false;
    render();
  }
}

async function commitMove(cid, targetTrack, start, origTrack) {
  try {
    const tgt = targetTrack || origTrack;
    let updated;
    if (tgt && tgt !== origTrack) {
      // 跨轨：一步解决（后端 moveClip 内已做防重叠）
      updated = await api.moveClip(state.pid, cid, tgt, start);
    } else {
      updated = await api.patchClip(state.pid, cid, { "timeline.start": start });
    }
    applyClip(updated, "update");
    bus.emit("timeline:changed");
    bus.emit("history:refresh");
  } catch (e) {
    console.error("[timeline] 移动失败", e);
    await refresh();
  }
}

async function commitTrim(cid, srcIn, srcOut, newStart) {
  try {
    // srcIn / srcOut 为 null 表示"不动这一侧"，后端 trim() 会跳过
    let updated = await api.trimClip(state.pid, cid, srcIn, srcOut);
    applyClip(updated, "update");
    if (newStart != null) {
      updated = await api.patchClip(state.pid, cid, { "timeline.start": newStart });
      applyClip(updated, "update");
    }
    bus.emit("timeline:changed");
    bus.emit("history:refresh");
  } catch (e) {
    console.error("[timeline] 裁剪失败", e);
    await refresh();
  }
}

// ---------------------------------------------------------------- 对外

export function selectedClipRange() {
  const cid = state.selectedClipId;
  if (!cid) return null;
  return { start: clipStart(cid), end: clipEnd(cid), duration: clipDuration(cid) };
}

export function rerender() {
  render();
}

export function scrollPlayheadIntoView() {
  if (!mScroll) return;
  const x = t2x(state.playhead);
  const left = mScroll.scrollLeft;
  const right = left + mScroll.clientWidth;
  if (x < left + 40 || x > right - 40) {
    mScroll.scrollLeft = Math.max(0, x - mScroll.clientWidth * 0.4);
    state.view.scrollLeft = mScroll.scrollLeft;
  }
}


// __EOF__
