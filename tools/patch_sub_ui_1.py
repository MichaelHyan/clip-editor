# -*- coding: utf-8 -*-
"""字幕前端接线 1：state 字段 + api 方法 + preview 绘制 + 时间轴渲染字幕轨。"""
import io

BASE = "D:/git/clip-editor"


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def sub(s, old, new, label):
    if old not in s:
        if new in s:
            print("  [skip] %s" % label)
            return s
        raise SystemExit("  [FAIL] 锚点缺失: %s" % label)
    print("  [ok]   %s" % label)
    return s.replace(old, new, 1)


# ================================================================ state.js
p = BASE + "/static/js/state.js"
s = read(p)

s = sub(s,
        '''  selectedClipId: null,
  selectedToolInstance: null,''',
        '''  selectedClipId: null,
  selectedToolInstance: null,
  selectedSubtitleId: null,''',
        "state 加 selectedSubtitleId")

write(p, s)

# ================================================================ api.js
p = BASE + "/static/js/api.js"
s = read(p)

s = sub(s,
        '  // 素材箱',
        '''  // 字幕
  subtitles: (pid) => GET(`/api/projects/${pid}/subtitles`),
  addSubtitle: (pid, body) => POST(`/api/projects/${pid}/subtitles`, body || {}),
  patchSubtitle: (pid, sid, body) => PATCH(`/api/projects/${pid}/subtitles/${sid}`, body || {}),
  delSubtitle: (pid, sid) => DEL(`/api/projects/${pid}/subtitles/${sid}`),

  // 素材箱''',
        "api 加字幕方法")

write(p, s)

# ================================================================ preview.js
p = BASE + "/static/js/preview.js"
s = read(p)

# import 字幕绘制
s = sub(s,
        'import { api, mediaUrl } from "./api.js";',
        'import { api, mediaUrl } from "./api.js";\nimport { subtitleAt, drawSubtitle } from "./subtitles.js";',
        "preview 导入字幕模块")

# 渲染末尾叠字幕
s = sub(s,
        '''  dirty = false;
}

function loop(now) {''',
        '''  // 字幕叠在最上层（不受片段变换影响）
  const sub = subtitleAt(absT);
  if (sub) {
    try {
      drawSubtitle(ctx, sub, outW, outH);
    } catch (e) {
      /* 忽略绘制异常，不影响画面 */
    }
  }

  dirty = false;
}

function loop(now) {''',
        "renderFrame 末尾绘制字幕")

write(p, s)

# ================================================================ timeline.js
p = BASE + "/static/js/timeline.js"
s = read(p)

# 1) 导入字幕模块
s = sub(s,
        'import * as assetsUi from "./assets_ui.js";',
        'import * as assetsUi from "./assets_ui.js";\nimport * as subtitles from "./subtitles.js";',
        "timeline 导入字幕模块")

# 2) renderLanes：字幕轨渲染字幕块
s = sub(s,
        '''    for (const c of clipsOfTrack(tr.id)) lane.appendChild(renderClipEl(c));
    mCanvas.appendChild(lane);''',
        '''    if (tr.type === "subtitle") {
      for (const sub of subtitles.listSubtitles()) {
        lane.appendChild(renderSubtitleEl(sub));
      }
    } else {
      for (const c of clipsOfTrack(tr.id)) lane.appendChild(renderClipEl(c));
    }
    mCanvas.appendChild(lane);''',
        "renderLanes 渲染字幕轨")

# 3) 字幕块渲染函数（放在 renderClipEl 前）
s = sub(s,
        '''function renderClipEl(c) {''',
        '''// 字幕块：可点击选中、可拖动改时间、可拖两边改时长
function renderSubtitleEl(sub) {
  const left = t2x(sub.start);
  const w = Math.max(6, t2x(sub.end - sub.start));
  const node = el("div", {
    class: "tl-sub",
    dataset: { sid: sub.id },
    style: { left: left + "px", width: w + "px" },
    title: (sub.text || "").slice(0, 60) + "\\n" + sub.start.toFixed(2) + "s ~ " + sub.end.toFixed(2) + "s",
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

function renderClipEl(c) {''',
        "字幕块渲染 + 拖拽")

# 4) 点击空白时清除字幕选中
s = sub(s,
        '''  if (state.playing) setPlaying(false);
  selectClip(null);
  startScrub(ev);''',
        '''  if (state.playing) setPlaying(false);
  selectClip(null);
  if (state.selectedSubtitleId) {
    state.selectedSubtitleId = null;
    bus.emit("selection:changed", null);
  }
  startScrub(ev);''',
        "空白点击清除字幕选中")

write(p, s)
print("done")

# __EOF__