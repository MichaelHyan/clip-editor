// 实时同步：SocketIO 优先，不可用时降级为轮询
// 所有服务端广播都汇聚到 state 的 apply* 函数；
// 自己的操作带 client_id，回环广播会被忽略以免抖动。

import { state, bus, applyClip, applyClipPatch, applyProject, applyProjectPatch, removeClipById, loadTools } from "./state.js";
import { clientId } from "./api.js";

let socket = null;
let pollTimer = null;
let connected = false;
let lastSeenRev = 0;

function isSelf(payload) {
  return !!(payload && payload.client_id && payload.client_id === clientId());
}

function setConnected(v) {
  if (connected === v) return;
  connected = v;
  bus.emit("socket:status", v);
}

function startPolling() {
  if (pollTimer) return;
  console.warn("[socket] 实时通道不可用，降级为 2s 轮询");
  pollTimer = setInterval(async () => {
    if (!state.pid || !state.ready) return;
    try {
      const { api } = await import("./api.js");
      const d = await api.getProject(state.pid);
      if (d.project.rev !== lastSeenRev) {
        lastSeenRev = d.project.rev;
        applyProject(d.project);
        for (const [cid, c] of Object.entries(d.clips || {})) applyClip(c, "update");
      }
    } catch (e) {
      /* 忽略轮询错误 */
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export function connect(pid) {
  if (!pid) return;
  if (typeof window.io !== "function") {
    setConnected(false);
    startPolling();
    return;
  }
  if (socket) {
    try {
      socket.emit("join", { project_id: pid });
    } catch (e) {
      /* 忽略 */
    }
    return;
  }

  socket = window.io({
    transports: ["websocket", "polling"],
    reconnection: true,
    reconnectionDelay: 800,
    reconnectionDelayMax: 4000,
    timeout: 12000,
  });

  socket.on("connect", () => {
    setConnected(true);
    stopPolling();
    socket.emit("join", { project_id: pid });
    bus.emit("socket:reconnected");
  });

  socket.on("disconnect", () => {
    setConnected(false);
    startPolling();
  });

  socket.on("connect_error", () => {
    setConnected(false);
    startPolling();
  });

  socket.on("project.updated", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    applyProject(p.project);
    lastSeenRev = p.project.rev || lastSeenRev;
  });

  socket.on("project.patched", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    applyProjectPatch(p.patch || {});
  });

  socket.on("clip.updated", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    if (p.action === "create" || p.action === "update") {
      applyClip(p.clip, "update");
    }
  });

  socket.on("clip.patched", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    applyClipPatch(p.clip_id, p.patch || {});
  });

  socket.on("clip.removed", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    removeClipById(p.clip_id);
  });

  socket.on("effect.registry.updated", () => {
    loadTools();
  });

  socket.on("playhead", (p) => {
    if (isSelf(p) || p.project_id !== state.pid) return;
    bus.emit("playhead:remote", p.t);
  });

  socket.on("history.updated", (p) => {
    if (p.project_id !== state.pid) return;
    state.history = p.info || state.history;
    bus.emit("history:changed", state.history);
  });

  socket.on("agent.event", (p) => {
    bus.emit("agent:event", p);
  });

  socket.on("server.hello", () => {});
}

export function disconnect() {
  stopPolling();
  if (socket) {
    try {
      socket.disconnect();
    } catch (e) {
      /* 忽略 */
    }
    socket = null;
  }
  setConnected(false);
}

export function emitPlayhead(t) {
  if (socket && connected && state.project && state.project.shared_playhead) {
    socket.emit("playhead", { project_id: state.pid, t, client_id: clientId() });
  }
}

export function isConnected() {
  return connected;
}


// __EOF__