// 全局状态：服务端数据的镜像 + 乐观更新 + 视图状态
// 设计原则：
//   1. 唯一真相源在后端，前端只做镜像，所有变更都经 api 落盘后回填
//   2. 乐观更新只用于拖拽等高频交互，409 时整体重同步
//   3. 视图状态（缩放/滚动/选中）不进后端

import { makeBus, clone, clamp, isNum } from "./util.js";
import { api } from "./api.js";
import { evalTrack } from "./keyframe.js";

export const bus = makeBus();

export const state = {
  pid: null,
  project: null,          // {id,name,settings,tracks,markers,playhead,rev}
  clips: {},              // cid -> clip
  durations: {},          // cid -> 秒
  totalDuration: 0,
  history: { undo: 0, redo: 0, undo_labels: [], redo_labels: [] },
  tools: {},              // toolId -> 工具定义
  toolList: [],           // 有序工具列表
  selectedClipId: null,
  selectedToolInstance: null,
  selectedSubtitleId: null,
  playhead: 0,            // 秒（时间轴绝对时间）
  playing: false,
  view: {
    pxPerSec: 80,
    scrollLeft: 0,
    trackHeight: 64,
    snapToFrames: true,
    snapToMarkers: true,
  },
  ready: false,
  saving: 0,
};

// ---------------------------------------------------------------- 查询

export function fps() {
  return (state.project && state.project.settings && state.project.settings.fps) || 60;
}

export function tracks() {
  if (!state.project) return [];
  return state.project.tracks.slice().sort((a, b) => (a.index || 0) - (b.index || 0));
}

export function tracksByType(type) {
  return tracks().filter((t) => t.type === type);
}

export function clip(cid) {
  return state.clips[cid] || null;
}

export function selectedClip() {
  return state.selectedClipId ? clip(state.selectedClipId) : null;
}

export function clipDuration(cid) {
  const d = state.durations[cid];
  if (isNum(d)) return d;
  const c = clip(cid);
  return c && c.timeline && isNum(c.timeline.duration) ? c.timeline.duration : 0;
}

export function clipStart(cid) {
  const c = clip(cid);
  return c && c.timeline && isNum(c.timeline.start) ? c.timeline.start : 0;
}

export function clipEnd(cid) {
  return clipStart(cid) + clipDuration(cid);
}

export function clipsOfTrack(trackId) {
  const c = state.project && state.project.tracks.find((t) => t.id === trackId);
  if (!c) return [];
  return (c.clips || [])
    .map(clip)
    .filter(Boolean)
    .sort((a, b) => clipStart(a.id) - clipStart(b.id));
}

// 时间轴绝对时间 -> 该片段内的局部时间（可能为负或超长）
export function localTime(cid, absT) {
  return absT - clipStart(cid);
}

export function isInClip(cid, absT) {
  const t = localTime(cid, absT);
  return t >= 0 && t <= clipDuration(cid);
}

// 解算片段某时刻的属性（前端预览用，与后端 resolve_at 语义一致）
export function resolveClipAt(cid, absT) {
  const c = clip(cid);
  if (!c) return null;
  const t = clamp(localTime(cid, absT), 0, Math.max(0, clipDuration(cid)));
  const v = c.video || {};
  const a = c.audio || {};
  const crop = v.crop || {};
  const out = {
    t,
    video: {
      enabled: v.enabled !== false,
      opacity: evalTrack(v.opacity, t, 1),
      scale: evalTrack(v.scale, t, 1),
      pos: [evalTrack(v.pos_x, t, 0), evalTrack(v.pos_y, t, 0)],
      rotation: evalTrack(v.rotation, t, 0),
      anchor: [evalTrack(v.anchor_x, t, 0.5), evalTrack(v.anchor_y, t, 0.5)],
      speed: evalTrack(v.speed, t, 1),
      srcIn: evalTrack(v.src_in, t, 0) || 0,
      srcOut: evalTrack(v.src_out, t, null),
      crop: {
        top: evalTrack(crop.top, t, 0),
        bottom: evalTrack(crop.bottom, t, 0),
        left: evalTrack(crop.left, t, 0),
        right: evalTrack(crop.right, t, 0),
      },
      mirror: [
        !!evalTrack(v.mirror_h, t, false),
        !!evalTrack(v.mirror_v, t, false),
      ],
      erode: evalTrack(v.erode, t, 0),
      erodeSoftness: evalTrack(v.erode_softness, t, 0),
      erodeAngle: evalTrack(v.erode_angle, t, 0),
      blend: v.blend || "normal",
    },
    audio: {
      enabled: a.enabled !== false,
      gain: evalTrack(a.gain, t, 0),
      pitch: evalTrack(a.pitch, t, 0),
      pan: evalTrack(a.pan, t, 0),
      speed: evalTrack(a.speed, t, 1),
      srcIn: evalTrack(a.src_in, t, 0) || 0,
      srcOut: evalTrack(a.src_out, t, null),
      fadeIn: evalTrack(a.fade_in, t, 0),
      fadeOut: evalTrack(a.fade_out, t, 0),
    },
    effects: [],
  };
  for (const fx of c.effects || []) {
    if (fx.enabled === false) continue;
    const params = {};
    for (const [k, tr] of Object.entries(fx.params || {})) {
      params[k] = evalTrack(tr, t, null);
    }
    out.effects.push({ id: fx.id, type: fx.type, params });
  }
  return out;
}

// 该路径在片段内是否有任何关键帧
export function trackOfPath(c, path) {
  if (!c) return null;
  const parts = String(path).split(".");
  let cur = c;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur == null || typeof cur !== "object") return null;
    cur = cur[parts[i]];
  }
  return cur ? cur[parts[parts.length - 1]] : null;
}

// ---------------------------------------------------------------- 加载

export async function loadProject(pid) {
  state.pid = pid;
  const data = await api.getProject(pid);
  state.project = data.project;
  state.clips = data.clips || {};
  state.durations = data.durations || {};
  state.totalDuration = data.total_duration || 0;
  state.history = data.history || state.history;
  state.playhead = data.project.playhead || 0;
  if (state.selectedClipId && !state.clips[state.selectedClipId]) {
    state.selectedClipId = null;
  }
  state.ready = true;
  await loadTools();
  bus.emit("project:loaded", state.project);
  bus.emit("timeline:changed");
  return state.project;
}

export async function refresh() {
  if (!state.pid) return;
  const data = await api.getProject(state.pid);
  state.project = data.project;
  state.clips = data.clips || {};
  state.durations = data.durations || {};
  state.totalDuration = data.total_duration || 0;
  state.history = data.history || state.history;
  bus.emit("timeline:changed");
  bus.emit("selection:changed", state.selectedClipId);
}

export async function loadTools() {
  try {
    const d = await api.tools();
    const list = (d && d.tools) || [];
    state.toolList = list;
    state.tools = {};
    for (const t of list) state.tools[t.id] = t;
    bus.emit("tools:changed", list);
  } catch (e) {
    console.warn("[state] 工具清单加载失败", e);
  }
}

// ---------------------------------------------------------------- 选择

export function selectClip(cid, opts = {}) {
  if (state.selectedClipId === cid && !opts.force) return;
  state.selectedClipId = cid || null;
  state.selectedToolInstance = null;
  bus.emit("selection:changed", state.selectedClipId);
  if (cid && opts.scrollIntoView) bus.emit("timeline:reveal", cid);
}

export function selectToolInstance(cid, iid) {
  state.selectedClipId = cid || null;
  state.selectedToolInstance = iid || null;
  bus.emit("selection:changed", state.selectedClipId);
}

// ---------------------------------------------------------------- 播放头

export function setPlayhead(t, opts = {}) {
  const max = Math.max(0, state.totalDuration);
  const nt = clamp(isNum(t) ? t : 0, 0, max);
  if (Math.abs(nt - state.playhead) < 1e-6 && !opts.force) return;
  state.playhead = nt;
  bus.emit("playhead:changed", nt);
  if (opts.broadcast) bus.emit("playhead:broadcast", nt);
}

export function setPlaying(v) {
  if (state.playing === !!v) return;
  state.playing = !!v;
  bus.emit("playing:changed", state.playing);
}

// ---------------------------------------------------------------- 视图

// 缩放范围：0.5 px/s（看整段长视频）~ 20000 px/s（逐帧精修）
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 20000;

export function setZoom(pxPerSec) {
  state.view.pxPerSec = clamp(pxPerSec, ZOOM_MIN, ZOOM_MAX);
  bus.emit("zoom:changed", state.view.pxPerSec);
}

export function zoomBy(factor, anchorSec) {
  const before = state.view.pxPerSec;
  const after = clamp(before * factor, ZOOM_MIN, ZOOM_MAX);
  state.view.pxPerSec = after;
  bus.emit("zoom:changed", after);
  if (isNum(anchorSec)) bus.emit("zoom:anchor", { anchorSec, before, after });
}

// ---------------------------------------------------------------- 变更应用（来自 socket 或本地）

export function applyClip(clipObj, action = "update") {
  if (!clipObj || !clipObj.id) return;
  if (action === "delete") {
    const old = state.clips[clipObj.id];
    const oldTrack = old && old.timeline ? old.timeline.track_id : null;
    delete state.clips[clipObj.id];
    delete state.durations[clipObj.id];
    if (oldTrack) syncTrackMembership(clipObj.id, oldTrack, null);
  } else {
    const prev = state.clips[clipObj.id];
    const oldTrack = prev && prev.timeline ? prev.timeline.track_id : null;
    const newTrack = clipObj.timeline ? clipObj.timeline.track_id : null;
    state.clips[clipObj.id] = clipObj;
    syncTrackMembership(clipObj.id, oldTrack, newTrack);
    syncTimelineStart(clipObj.id);
  }
  recomputeDuration(clipObj.id);
  bus.emit("clip:changed", { clip: clipObj, action });
  bus.emit("timeline:changed");
}

// 把 cid 在 project.tracks 里的归属从 oldTrack 移到 newTrack
// 这是渲染的正确性基础：renderFrame 读 clipsOfTrack(trackId) 用的是轨道数组
export function syncTrackMembership(cid, oldTrack, newTrack) {
  if (!state.project || !Array.isArray(state.project.tracks)) return;
  if (oldTrack === newTrack && oldTrack) return;
  for (const tr of state.project.tracks) {
    if (!Array.isArray(tr.clips)) tr.clips = [];
    if (tr.id !== newTrack) {
      const i = tr.clips.indexOf(cid);
      if (i >= 0) tr.clips.splice(i, 1);
    }
  }
  if (newTrack) {
    const nt = state.project.tracks.find((t) => t.id === newTrack);
    if (nt) {
      if (!Array.isArray(nt.clips)) nt.clips = [];
      if (!nt.clips.includes(cid)) nt.clips.push(cid);
    }
  }
}

// 片段移动后按 start 重排所在轨道，保证渲染顺序与时间轴一致
export function syncTimelineStart(cid) {
  const c = state.clips[cid];
  if (!c || !state.project) return;
  const tid = c.timeline && c.timeline.track_id;
  if (!tid) return;
  const tr = state.project.tracks.find((t) => t.id === tid);
  if (!tr || !Array.isArray(tr.clips)) return;
  tr.clips.sort((a, b) => clipStart(a) - clipStart(b));
}

export function removeClipById(cid) {
  delete state.clips[cid];
  delete state.durations[cid];
  if (state.selectedClipId === cid) {
    state.selectedClipId = null;
    bus.emit("selection:changed", null);
  }
  bus.emit("timeline:changed");
}

export function applyClipPatch(cid, patch) {
  const c = state.clips[cid];
  if (!c || !patch) return;
  for (const [path, value] of Object.entries(patch)) {
    const parts = path.split(".");
    let node = c;
    for (let i = 0; i < parts.length - 1; i++) {
      if (node[parts[i]] == null || typeof node[parts[i]] !== "object") node[parts[i]] = {};
      node = node[parts[i]];
    }
    node[parts[parts.length - 1]] = value;
  }
  recomputeDuration(cid);
  bus.emit("clip:changed", { clip: c, action: "patch", patch });
  bus.emit("timeline:changed");
}

export function applyProject(pj) {
  if (!pj) return;
  state.project = pj;
  bus.emit("project:changed", pj);
  bus.emit("timeline:changed");
}

export function applyProjectPatch(patch) {
  if (!state.project || !patch) return;
  for (const [k, v] of Object.entries(patch)) {
    state.project[k] = v;
  }
  bus.emit("project:changed", state.project);
  bus.emit("timeline:changed");
}

export function recomputeDuration(cid) {
  const c = state.clips[cid];
  if (!c) return;
  const tl = c.timeline || {};
  if (isNum(tl.duration)) {
    state.durations[cid] = tl.duration;
    return;
  }
  const sec = (c.media && c.media.kind) === "audio" ? c.audio : c.video;
  const probe = (c.media && c.media.probe) || {};
  const sIn = evalTrack(sec && sec.src_in, 0, 0) || 0;
  let sOut = evalTrack(sec && sec.src_out, 0, null);
  if (sOut == null) sOut = probe.duration;
  const spd = evalTrack(sec && sec.speed, 0, 1) || 1;
  const d = sOut == null ? 0 : Math.max(0, (sOut - sIn) / spd);
  state.durations[cid] = d;
  tl.duration = d;
}

export function recomputeTotal() {
  let end = 0;
  for (const cid of Object.keys(state.clips)) {
    end = Math.max(end, clipEnd(cid));
  }
  state.totalDuration = end;
  bus.emit("timeline:changed");
}

// ---------------------------------------------------------------- 写操作封装（落盘 + 回填）

async function mutate(fn) {
  state.saving++;
  bus.emit("saving:changed", state.saving);
  try {
    return await fn();
  } finally {
    state.saving--;
    bus.emit("saving:changed", state.saving);
  }
}

export async function patchClip(cid, patch, opts = {}) {
  const c = clip(cid);
  if (!c) return null;
  return mutate(async () => {
    try {
      const updated = await api.patchClip(state.pid, cid, patch, opts.rev === false ? undefined : c.rev);
      applyClip(updated, "update");
      bus.emit("history:refresh");
      return updated;
    } catch (e) {
      if (e.status === 409 && e.payload && e.payload.clip) {
        applyClip(e.payload.clip, "update");
        bus.emit("conflict", { cid, patch });
      }
      throw e;
    }
  });
}

export async function addKeyframe(cid, path, tLocal, value, e = "linear", h = null) {
  return mutate(async () => {
    const updated = await api.addKeyframe(state.pid, cid, path, tLocal, value, e, h);
    applyClip(updated, "update");
    bus.emit("history:refresh");
    return updated;
  });
}

export async function delKeyframe(cid, path, tLocal) {
  return mutate(async () => {
    const updated = await api.delKeyframe(state.pid, cid, path, tLocal);
    applyClip(updated, "update");
    bus.emit("history:refresh");
    return updated;
  });
}

export async function applyToolToClip(cid, toolId, params) {
  return mutate(async () => {
    const d = await api.applyTool(state.pid, cid, toolId, params);
    applyClip(d.clip, "update");
    bus.emit("history:refresh");
    return d.instance;
  });
}

export async function patchToolInstance(cid, iid, body) {
  return mutate(async () => {
    const updated = await api.patchTool(state.pid, cid, iid, body);
    applyClip(updated, "update");
    bus.emit("history:refresh");
    return updated;
  });
}

export async function removeToolInstance(cid, iid) {
  return mutate(async () => {
    const updated = await api.removeTool(state.pid, cid, iid);
    applyClip(updated, "update");
    bus.emit("history:refresh");
    return updated;
  });
}

export async function undo() {
  return mutate(async () => {
    const d = await api.undo(state.pid);
    await refresh();
    bus.emit("history:changed", d.info);
    return d;
  });
}

export async function redo() {
  return mutate(async () => {
    const d = await api.redo(state.pid);
    await refresh();
    bus.emit("history:changed", d.info);
    return d;
  });
}

export async function refreshHistory() {
  if (!state.pid) return;
  try {
    state.history = await api.history(state.pid);
    bus.emit("history:changed", state.history);
  } catch (e) {
    /* 忽略 */
  }
}

bus.on("history:refresh", () => {
  refreshHistory();
});


// __EOF__