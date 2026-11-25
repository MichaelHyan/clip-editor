// REST 客户端
// 统一封装 fetch，自动 JSON、错误归一、客户端 id 透传。

let CLIENT_ID = "ui_" + Math.random().toString(36).slice(2, 10);

export function clientId() {
  return CLIENT_ID;
}

export function setClientId(id) {
  if (id) CLIENT_ID = String(id);
}

async function request(method, url, { body, query } = {}) {
  let full = url;
  if (query) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v == null) continue;
      q.set(k, v);
    }
    const s = q.toString();
    if (s) full += (url.includes("?") ? "&" : "?") + s;
  }
  const init = { method, headers: { "X-Client-Id": CLIENT_ID } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify({ ...body, client_id: CLIENT_ID });
  }
  const res = await fetch(full, init);
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok || (data && data.ok === false)) {
    const msg = (data && data.error) || "HTTP " + res.status;
    const err = new Error(msg);
    err.status = res.status;
    err.payload = data;
    throw err;
  }
  return data && data.data !== undefined ? data.data : data;
}

const GET = (url, query) => request("GET", url, { query });
const POST = (url, body) => request("POST", url, { body: body || {} });
const PATCH = (url, body) => request("PATCH", url, { body: body || {} });
const DEL = (url, body) => request("DELETE", url, { body: body || {} });

// 流媒体 URL（不经 fetch，直接给 <video>/<img>）
export function mediaUrl(path, proxy = false, audio = false) {
  const q = new URLSearchParams({ path });
  if (proxy) q.set("proxy", "1");
  if (audio) q.set("audio", "1");
  return "/api/media/stream?" + q.toString();
}
export function thumbUrl(path, t = 0, w = 320) {
  const q = new URLSearchParams({ path, t: String(t), w: String(w) });
  return "/api/media/thumbnail?" + q.toString();
}

export const api = {
  // 健康
  health: () => GET("/api/health"),

  // 项目
  listProjects: () => GET("/api/projects"),
  createProject: (name, settings) => POST("/api/projects", { name, settings }),
  getProject: (pid) => GET(`/api/projects/${pid}`),
  patchProject: (pid, patch) => PATCH(`/api/projects/${pid}`, { patch }),
  deleteProject: (pid) => DEL(`/api/projects/${pid}`),

  // 轨道
  addTrack: (pid, type, name, index) => POST(`/api/projects/${pid}/tracks`, { type, name, index }),
  delTrack: (pid, tid) => DEL(`/api/projects/${pid}/tracks/${tid}`),
  patchTrack: (pid, tid, patch) =>
    PATCH(`/api/projects/${pid}/tracks/${tid}`, { patch }),
  reorderTracks: (pid, order) => POST(`/api/projects/${pid}/tracks/reorder`, { order }),

  // 标记
  addMarker: (pid, t, label, color) => POST(`/api/projects/${pid}/markers`, { t, label, color }),
  delMarker: (pid, mid) => DEL(`/api/projects/${pid}/markers/${mid}`),

  // 片段
  listClips: (pid) => GET(`/api/projects/${pid}/clips`),
  importClip: (pid, path, opts) => POST(`/api/projects/${pid}/clips`, { path, ...(opts || {}) }),
  getClip: (pid, cid) => GET(`/api/projects/${pid}/clips/${cid}`),
  patchClip: (pid, cid, patch, rev) => PATCH(`/api/projects/${pid}/clips/${cid}`, { patch, rev }),
  delClip: (pid, cid) => DEL(`/api/projects/${pid}/clips/${cid}`),
  resolveClip: (pid, cid, t) => GET(`/api/projects/${pid}/clips/${cid}/resolve`, { t }),
  bakeClip: (pid, cid, path, t0, t1, fps) =>
    GET(`/api/projects/${pid}/clips/${cid}/bake`, { path, t0, t1, fps }),
  duplicateClip: (pid, cid, opts) => POST(`/api/projects/${pid}/clips/${cid}/duplicate`, opts || {}),
  splitClip: (pid, cid, t) => POST(`/api/projects/${pid}/clips/${cid}/split`, { t }),
  trimClip: (pid, cid, srcIn, srcOut) =>
    POST(`/api/projects/${pid}/clips/${cid}/trim`, { src_in: srcIn, src_out: srcOut }),
  moveClip: (pid, cid, trackId, start) =>
    POST(`/api/projects/${pid}/clips/${cid}/move`, { track_id: trackId, start }),

  // 关键帧
  addKeyframe: (pid, cid, path, t, value, e, h) =>
    POST(`/api/projects/${pid}/clips/${cid}/keyframe`, { path, t, value, e, h }),
  delKeyframe: (pid, cid, path, t) =>
    DEL(`/api/projects/${pid}/clips/${cid}/keyframe`, { path, t }),

  // 效果（内部用）
  addEffect: (pid, cid, type, params) => POST(`/api/projects/${pid}/clips/${cid}/effects`, { type, params }),
  patchEffect: (pid, cid, fxid, body) => PATCH(`/api/projects/${pid}/clips/${cid}/effects/${fxid}`, body),
  delEffect: (pid, cid, fxid) => DEL(`/api/projects/${pid}/clips/${cid}/effects/${fxid}`),

  // 片段内标记
  addClipMarker: (pid, cid, t, label, color) =>
    POST(`/api/projects/${pid}/clips/${cid}/markers`, { t, label, color }),
  delClipMarker: (pid, cid, mid) => DEL(`/api/projects/${pid}/clips/${cid}/markers/${mid}`),

  // 片段库
  saveToLibrary: (pid, cid, name) => POST(`/api/projects/${pid}/clips/${cid}/save-to-library`, { name }),
  fromLibrary: (pid, id, opts) => POST(`/api/projects/${pid}/clips/from-library`, { id, ...(opts || {}) }),
  library: (pid) => GET(`/api/projects/${pid}/library`),

  // 历史
  history: (pid) => GET(`/api/projects/${pid}/history`),
  undo: (pid) => POST(`/api/projects/${pid}/undo`, {}),
  redo: (pid) => POST(`/api/projects/${pid}/redo`, {}),

  // 快照
  snapshot: (pid, tag) => POST(`/api/projects/${pid}/snapshot`, { tag }),
  snapshots: (pid) => GET(`/api/projects/${pid}/snapshots`),
  restoreSnapshot: (pid, file) => POST(`/api/projects/${pid}/snapshot/restore`, { file }),

  // 媒体
  browse: (path, limit) => GET("/api/media/browse", { path, limit }),
  probe: (path) => GET("/api/media/probe", { path }),
  waveform: (path, buckets) => GET("/api/media/waveform", { path, buckets }),
  proxyState: (path) => GET("/api/media/proxy", { path }),
  playable: (path) => GET("/api/media/playable", { path }),
  buildProxy: (path, opts) => POST("/api/media/proxy" + queryOf({ path, ...(opts || {}) }), {}),
  audioProxy: (path, force) => POST("/api/media/audio-proxy", { path, force: !!force }),
  mediaConfig: () => GET("/api/media/config"),

  // 效果/工具（内部注册表）
  effects: (scope) => GET("/api/effects", { scope }),
  registerEffect: (body) => POST("/api/effects", body),
  updateEffect: (etype, definition) => PATCH(`/api/effects/${etype}`, { definition }),
  deleteEffect: (etype) => DEL(`/api/effects/${etype}`),

  // 字幕
  subtitles: (pid) => GET(`/api/projects/${pid}/subtitles`),
  addSubtitle: (pid, body) => POST(`/api/projects/${pid}/subtitles`, body || {}),
  patchSubtitle: (pid, sid, body) => PATCH(`/api/projects/${pid}/subtitles/${sid}`, body || {}),
  delSubtitle: (pid, sid) => DEL(`/api/projects/${pid}/subtitles/${sid}`),

  // 素材箱
  listAssets: (pid) => GET(`/api/projects/${pid}/assets`),
  addAsset: (pid, path, name) => POST(`/api/projects/${pid}/assets`, { path, name }),
  removeAsset: (pid, aid) => DEL(`/api/projects/${pid}/assets/${aid}`),
  refreshAssets: (pid, assetId) => POST(`/api/projects/${pid}/assets/refresh`, { asset_id: assetId }),
  assetToTimeline: (pid, aid, opts) =>
    POST(`/api/projects/${pid}/assets/${aid}/to-timeline`, opts || {}),

  // 导出
  exportPresets: () => GET("/api/export/presets"),
  exportStart: (pid, opts) => POST(`/api/export/${pid}`, opts || {}),
  exportJobs: (pid) => GET("/api/export/jobs", { project_id: pid }),
  exportJob: (jid) => GET(`/api/export/jobs/${jid}`),
  exportCancel: (jid) => POST(`/api/export/jobs/${jid}/cancel`, {}),
  exportDelete: (jid) => DEL(`/api/export/jobs/${jid}`),
  exportPreviewCmd: (pid, opts) => GET(`/api/export/preview-cmd/${pid}`, opts || {}),
  exportDownloadUrl: (jid) => `/api/export/jobs/${jid}/download`,

  // agent 工具接口
  tools: (opts) => GET("/api/agent/tools", opts || {}),
  registerTool: (def) => POST("/api/agent/tools", def),
  getTool: (id) => GET(`/api/agent/tools/${id}`),
  deleteTool: (id) => DEL(`/api/agent/tools/${id}`),
  agentContext: (pid) => GET("/api/agent/context", { project_id: pid }),
  agentClip: (pid, cid) => GET(`/api/agent/clip/${cid}`, { project_id: pid }),
  applyTool: (pid, cid, toolId, params) =>
    POST(`/api/agent/clip/${cid}/apply-tool`, { project_id: pid, tool_id: toolId, params }),
  patchTool: (pid, cid, iid, body) =>
    PATCH(`/api/agent/clip/${cid}/tool/${iid}`, { project_id: pid, ...(body || {}) }),
  removeTool: (pid, cid, iid) => DEL(`/api/agent/clip/${cid}/tool/${iid}`, { project_id: pid }),
};

function queryOf(obj) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(obj || {})) {
    if (v == null) continue;
    q.set(k, v);
  }
  const s = q.toString();
  return s ? "?" + s : "";
}


// __EOF__