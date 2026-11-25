// 字幕系统：时间轴渲染、预览绘制、编辑面板
// 字幕存在 project.subtitles[]，不占 clip 体系，由 SUB 轨统一管理。

import { state, bus, refresh } from "./state.js";
import { api } from "./api.js";
import { el, clear, clamp } from "./util.js";

// ---------------------------------------------------------------- 查询

export function listSubtitles() {
  return (state.project && state.project.subtitles) || [];
}

// 播放头处应显示的字幕（多条重叠时取第一条）
export function subtitleAt(t) {
  for (const s of listSubtitles()) {
    if (t >= s.start - 1e-6 && t <= s.end + 1e-6) return s;
  }
  return null;
}

export function selectedSubtitle() {
  const sid = state.selectedSubtitleId;
  if (!sid) return null;
  return listSubtitles().find((x) => x.id === sid) || null;
}

// ---------------------------------------------------------------- 预览绘制

// 在预览画布上绘制字幕。x/y 是 0~1 归一化的文本锚点（y 为基线中心）。
export function drawSubtitle(c2d, sub, W, H) {
  if (!sub || !sub.text) return;
  const st = sub.style || {};
  const fontSize = clamp(Number(st.fontSize) || 48, 8, 400);
  // 字号按画布宽度归一化：48px 对应 1080p 宽度的感觉
  const px = (fontSize / 1920) * W;
  const text = String(sub.text);

  c2d.save();
  c2d.font = `${st.bold ? "700 " : ""}${px}px "Microsoft YaHei","PingFang SC",sans-serif`;
  c2d.textAlign = st.align || "center";
  c2d.textBaseline = "middle";

  const x = clamp(Number(st.x ?? 0.5), 0, 1) * W;
  const y = clamp(Number(st.y ?? 0.86), 0, 1) * H;

  const lines = text.split("\n");
  const lineH = px * 1.28;
  const totalH = lineH * lines.length;
  let baseY = y - totalH / 2 + lineH / 2;

  for (const line of lines) {
    const outline = clamp(Number(st.outline) || 0, 0, 20) * (px / 48);
    if (outline > 0.2) {
      c2d.lineWidth = outline * 2;
      c2d.lineJoin = "round";
      c2d.miterLimit = 2;
      c2d.strokeStyle = st.outlineColor || "#000000";
      c2d.strokeText(line, x, baseY);
    }
    c2d.fillStyle = st.color || "#ffffff";
    c2d.fillText(line, x, baseY);
    baseY += lineH;
  }
  c2d.restore();
}

// ---------------------------------------------------------------- 配置

// 新建字幕：默认放在播放头处，持续 3 秒
export async function addSubtitleAtPlayhead(text) {
  if (!state.pid) return null;
  const start = state.playhead;
  const end = start + 3;
  try {
    const sub = await api.addSubtitle(state.pid, { start, end, text: text || "新字幕" });
    await refresh();
    state.selectedSubtitleId = sub.id;
    bus.emit("subtitle:changed", sub);
    return sub;
  } catch (e) {
    console.error("[subtitles] 添加失败", e);
    bus.emit("toast", { msg: "添加字幕失败：" + e.message, kind: "error" });
    return null;
  }
}

export async function patchSubtitle(sid, patch) {
  if (!state.pid || !sid) return null;
  try {
    const sub = await api.patchSubtitle(state.pid, sid, patch);
    await refresh();
    bus.emit("subtitle:changed", sub);
    return sub;
  } catch (e) {
    console.error("[subtitles] 修改失败", e);
    return null;
  }
}

export async function removeSubtitle(sid) {
  if (!state.pid || !sid) return null;
  try {
    const left = await api.delSubtitle(state.pid, sid);
    if (state.selectedSubtitleId === sid) state.selectedSubtitleId = null;
    await refresh();
    bus.emit("subtitle:changed", null);
    return left;
  } catch (e) {
    console.error("[subtitles] 删除失败", e);
    return null;
  }
}

export function selectSubtitle(sid) {
  state.selectedSubtitleId = sid || null;
  if (sid) state.selectedClipId = null;
  bus.emit("selection:changed", sid ? "subtitle:" + sid : null);
}

// __EOF__