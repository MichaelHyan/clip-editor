// 右侧属性检查器
// 四个页签：视频 / 音频 / 效果 / 标记
// 每个可动参数显示为一行：数值控件 + 关键帧按钮(◆)
//   - 静态值：直接改，落盘 video.xxx = 值
//   - 关键帧：按钮高亮，显示当前播放头处的解算值，修改即在播放头处写/改关键帧
// 效果页签按 state.toolList 动态生成，agent 注册新工具后自动出现，无需刷新页面

import {
  state, bus, clip, selectedClip, clipStart, clipDuration, resolveClipAt,
  patchClip, addKeyframe, delKeyframe, applyToolToClip, patchToolInstance,
  removeToolInstance, selectToolInstance,
} from "./state.js";
import { el, clear, clamp, round, timecode } from "./util.js";
import { evalTrack, hasKeys, allTimes } from "./keyframe.js";
import * as subtitles from "./subtitles.js";

let root = null;
let activeTab = "video";
let suppress = false;

const VIDEO_PARAMS = [
  { path: "video.scale", label: "缩放", min: 0.05, max: 5, step: 0.01 },
  { path: "video.pos_x", label: "X 位置", min: -4000, max: 4000, step: 1 },
  { path: "video.pos_y", label: "Y 位置", min: -4000, max: 4000, step: 1 },
  { path: "video.rotation", label: "旋转", min: -360, max: 360, step: 1, unit: "°" },
  { path: "video.opacity", label: "透明度", min: 0, max: 1, step: 0.01 },
  { path: "video.crop.top", label: "裁剪 上", min: 0, max: 0.95, step: 0.01 },
  { path: "video.crop.bottom", label: "裁剪 下", min: 0, max: 0.95, step: 0.01 },
  { path: "video.crop.left", label: "裁剪 左", min: 0, max: 0.95, step: 0.01 },
  { path: "video.crop.right", label: "裁剪 右", min: 0, max: 0.95, step: 0.01 },
  { path: "video.speed", label: "倍速", min: 0.1, max: 8, step: 0.01, unit: "x" },
  { path: "video.erode", label: "擦除", min: 0, max: 1, step: 0.01 },
  { path: "video.erode_softness", label: "羽化", min: 0, max: 1, step: 0.01 },
];

const VIDEO_BOOL = [
  { path: "video.mirror_h", label: "水平镜像" },
  { path: "video.mirror_v", label: "垂直镜像" },
];

const AUDIO_PARAMS = [
  { path: "audio.gain", label: "响度", min: -60, max: 24, step: 0.1, unit: "dB" },
  { path: "audio.pitch", label: "音调", min: -12, max: 12, step: 0.1, unit: "半音" },
  { path: "audio.speed", label: "倍速", min: 0.1, max: 8, step: 0.01, unit: "x" },
  { path: "audio.pan", label: "声像", min: -1, max: 1, step: 0.01 },
  { path: "audio.fade_in", label: "淡入", min: 0, max: 10, step: 0.01, unit: "s" },
  { path: "audio.fade_out", label: "淡出", min: 0, max: 10, step: 0.01, unit: "s" },
];

// ---------------------------------------------------------------- 工具函数

function getPath(obj, path) {
  const parts = String(path).split(".");
  let cur = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

function resolveTrack(clipObj, path) {
  // effects.<iid>.<param> 需要先按 id 找到效果实例（effects 是数组）
  if (typeof path === "string" && path.startsWith("effects.")) {
    const parts = path.split(".");
    const iid = parts[1];
    const pname = parts.slice(2).join(".");
    const fx = (clipObj.effects || []).find((x) => x.id === iid);
    return fx && fx.params ? fx.params[pname] : undefined;
  }
  return getPath(clipObj, path);
}

function localT() {
  const cid = state.selectedClipId;
  if (!cid) return 0;
  return clamp(state.playhead - clipStart(cid), 0, Math.max(0, clipDuration(cid)));
}

// ---------------------------------------------------------------- 字幕编辑

function buildSubtitleEditor(sub) {
  const st = sub.style || {};
  const head = el("div", { class: "insp-header" }, [
    el("div", { class: "insp-title" }, "字幕"),
    el("div", { class: "insp-sub" },
      `${sub.start.toFixed(2)}s ~ ${sub.end.toFixed(2)}s · ${(sub.end - sub.start).toFixed(2)}s`),
  ]);
  root.appendChild(head);
  root.appendChild(el("div", { class: "insp-tabs" }, [
    el("button", { class: "insp-tab active" }, "字幕"),
  ]));

  const body = el("div", { class: "insp-body" });

  // 文本
  body.appendChild(el("div", { class: "insp-section-title" }, "内容"));
  const ta = el("textarea", { class: "insp-textarea", rows: "3",
    placeholder: "输入字幕文字，支持换行" });
  ta.value = sub.text || "";
  let saveTimer = null;
  const doSave = async () => {
    const txt = ta.value;
    if (txt === (sub.text || "")) return;
    await subtitles.patchSubtitle(sub.id, { text: txt });
    build();
  };
  ta.addEventListener("input", () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(doSave, 700);
  });
  ta.addEventListener("blur", doSave);
  ta.addEventListener("keydown", (ev) => ev.stopPropagation());
  body.appendChild(ta);

  // 时间
  body.appendChild(el("div", { class: "insp-section-title" }, "时间"));
  body.appendChild(timeRow("起点", sub.start, async (v) => {
    await subtitles.patchSubtitle(sub.id, { start: v });
    build();
  }));
  body.appendChild(timeRow("终点", sub.end, async (v) => {
    await subtitles.patchSubtitle(sub.id, { end: v });
    build();
  }));

  // 样式
  body.appendChild(el("div", { class: "insp-section-title" }, "样式"));
  body.appendChild(styleRow("字号", "number", st.fontSize, { min: 8, max: 200, step: 1 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { fontSize: v } }); build(); }));
  body.appendChild(styleRow("颜色", "color", st.color || "#ffffff", {},
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { color: v } }); build(); }));
  body.appendChild(styleRow("描边色", "color", st.outlineColor || "#000000", {},
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { outlineColor: v } }); build(); }));
  body.appendChild(styleRow("描边宽", "number", st.outline, { min: 0, max: 12, step: 0.5 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { outline: v } }); build(); }));
  body.appendChild(styleRow("水平位置", "number", st.x != null ? st.x : 0.5,
    { min: 0, max: 1, step: 0.01 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { x: v } }); build(); }));
  body.appendChild(styleRow("垂直位置", "number", st.y != null ? st.y : 0.86,
    { min: 0, max: 1, step: 0.01 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { y: v } }); build(); }));

  // 对齐
  const alignRow = el("div", { class: "insp-param" });
  alignRow.appendChild(el("label", { class: "insp-label" }, "对齐"));
  const alignSel = el("select", { class: "insp-select" });
  for (const [v, l] of [["left", "左"], ["center", "中"], ["right", "右"]]) {
    const o = el("option", { value: v }, l);
    if ((st.align || "center") === v) o.selected = true;
    alignSel.appendChild(o);
  }
  alignSel.addEventListener("change", async () => {
    await subtitles.patchSubtitle(sub.id, { style: { align: alignSel.value } });
    build();
  });
  alignRow.appendChild(alignSel);
  body.appendChild(alignRow);

  // 删除
  const delBtn = el("button", { class: "insp-btn danger" }, "删除此字幕");
  delBtn.addEventListener("click", async () => {
    if (!confirm("删除这条字幕？")) return;
    await subtitles.removeSubtitle(sub.id);
    build();
  });
  body.appendChild(delBtn);

  root.appendChild(body);
}

function timeRow(label, value, onCommit) {
  const row = el("div", { class: "insp-param" });
  row.appendChild(el("label", { class: "insp-label" }, label));
  const num = el("input", {
    type: "number", class: "insp-num", min: 0, step: 0.05,
    value: Math.round(value * 100) / 100,
  });
  num.addEventListener("change", () => onCommit(Math.max(0, Number(num.value) || 0)));
  num.addEventListener("keydown", (ev) => ev.stopPropagation());
  row.appendChild(num);
  const btn = el("button", { class: "insp-kf", title: "取播放头" }, "\u23F1");
  btn.addEventListener("click", () => {
    const t = Math.round(state.playhead * 100) / 100;
    num.value = t;
    onCommit(t);
  });
  row.appendChild(btn);
  return row;
}

function styleRow(label, type, value, opts, onCommit) {
  const row = el("div", { class: "insp-param" });
  row.appendChild(el("label", { class: "insp-label" }, label));
  const inp = el("input", {
    type, class: type === "color" ? "insp-color" : "insp-num",
    ...(opts || {}),
  });
  inp.value = value != null ? value : "";
  inp.addEventListener("change", () => {
    const v = type === "color" ? inp.value : Number(inp.value);
    if (type === "number" && !Number.isFinite(v)) return;
    onCommit(v);
  });
  inp.addEventListener("keydown", (ev) => ev.stopPropagation());
  row.appendChild(inp);
  return row;
}

// 片段改名：就地输入框，回车/失焦提交，Esc 取消
function startRenameClip(c, titleEl) {
  const input = el("input", {
    class: "insp-title-input",
    value: c.name || "",
  });
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== c.name) {
      try {
        const { patchClip } = await import("./state.js");
        await patchClip(c.id, { name: next });
      } catch (e) {
        console.error("[inspector] 改名失败", e);
      }
    }
    build();
  };

  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") { ev.preventDefault(); commit(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); commit(false); }
  });
  input.addEventListener("blur", () => commit(true));
}

// ---------------------------------------------------------------- 初始化

export function init() {
  root = document.getElementById("inspector");
  if (!root) {
    console.warn("[inspector] #inspector 不存在");
    return;
  }
  build();

  bus.on("selection:changed", () => { if (!suppress) build(); });
  bus.on("subtitle:changed", () => { if (!suppress) build(); });
  bus.on("clip:changed", () => { if (!suppress) refreshValues(); });
  bus.on("playhead:changed", () => { if (!suppress) refreshValues(); });
  bus.on("project:loaded", () => { if (!suppress) build(); });
  bus.on("tools:changed", () => { if (!suppress) { buildTabs(); build(); } });
}

function build() {
  clear(root);

  // 字幕优先：选中字幕时显示字幕编辑页
  const sub = state.selectedSubtitleId
    ? (subtitles.listSubtitles().find((x) => x.id === state.selectedSubtitleId) || null)
    : null;
  if (sub) {
    buildSubtitleEditor(sub);
    return;
  }

  const c = selectedClip();
  if (!c) {
    root.appendChild(el("div", { class: "insp-empty" },
      "未选中片段\n在时间轴上点选一个片段以编辑属性\n\n要加字幕：点 SUB 轨旁的「+ 字幕」"));
    return;
  }

  const titleEl = el("div", { class: "insp-title", title: "双击改名" },
    c.name || c.id);
  titleEl.addEventListener("dblclick", () => startRenameClip(c, titleEl));
  root.appendChild(el("div", { class: "insp-header" }, [
    titleEl,
    el("div", { class: "insp-sub" }, [
      `时长 ${clipDuration(c.id).toFixed(2)}s`,
      ` · 起点 ${clipStart(c.id).toFixed(2)}s`,
      c.media && c.media.kind ? ` · ${c.media.kind}` : "",
    ].join("")),
  ]));

  buildTabs();

  const body = el("div", { class: "insp-body", dataset: { tab: activeTab } });
  if (activeTab === "video") buildVideo(body, c);
  else if (activeTab === "audio") buildAudio(body, c);
  else if (activeTab === "effects") buildEffects(body, c);
  else if (activeTab === "markers") buildMarkers(body, c);
  root.appendChild(body);

  refreshValues();
}

function buildTabs() {
  const tabs = [
    ["video", "视频"],
    ["audio", "音频"],
    ["effects", "效果"],
    ["markers", "标记"],
  ];
  const bar = el("div", { class: "insp-tabs" });
  for (const [id, label] of tabs) {
    bar.appendChild(el("button", {
      class: "insp-tab" + (activeTab === id ? " active" : ""),
      onclick: () => { activeTab = id; build(); },
    }, label));
  }
  if (root.querySelector(".insp-tabs")) root.querySelector(".insp-tabs").replaceWith(bar);
  else root.appendChild(bar);
}

// ---------------------------------------------------------------- 参数行

function buildParamRow(parent, c, spec) {
  const row = el("div", { class: "insp-param", dataset: { path: spec.path } });
  row.appendChild(el("label", { class: "insp-label" }, spec.label));

  const track = getPath(c, spec.path);
  const isKf = hasKeys(track);
  const cur = evalTrack(track, localT(), spec.min ?? 0);

  const input = el("input", {
    class: "insp-input",
    type: "range",
    min: spec.min, max: spec.max, step: spec.step,
    value: cur,
    dataset: { path: spec.path, kind: "range" },
  });
  const num = el("input", {
    class: "insp-num",
    type: "number",
    min: spec.min, max: spec.max, step: spec.step,
    value: round(cur, 3),
    dataset: { path: spec.path, kind: "num" },
  });
  if (spec.unit) row.appendChild(el("span", { class: "insp-unit" }, spec.unit));

  const kfBtn = el("button", {
    class: "insp-kf" + (isKf ? " active" : ""),
    title: isKf ? "在播放头处增/删关键帧" : "转为关键帧（在播放头处打点）",
    dataset: { path: spec.path },
  }, "\u25C6");

  const onInput = (ev) => {
    const v = clamp(parseFloat(ev.target.value), spec.min, spec.max);
    if (Number.isNaN(v)) return;
    if (ev.target === input) num.value = round(v, 3);
    else input.value = v;
    applyValue(c.id, spec.path, v);
  };
  input.addEventListener("input", onInput);
  num.addEventListener("change", onInput);

  kfBtn.addEventListener("click", () => toggleKeyframe(c.id, spec.path, track));

  row.appendChild(input);
  row.appendChild(num);
  row.appendChild(kfBtn);
  parent.appendChild(row);
}

function buildBoolRow(parent, c, spec) {
  const track = getPath(c, spec.path);
  const cur = !!evalTrack(track, localT(), false);
  const row = el("div", { class: "insp-param", dataset: { path: spec.path } });
  row.appendChild(el("label", { class: "insp-label" }, spec.label));
  const box = el("input", {
    type: "checkbox",
    class: "insp-check",
    dataset: { path: spec.path },
  });
  box.checked = cur;
  box.addEventListener("change", () => applyValue(c.id, spec.path, box.checked));
  const kfBtn = el("button", {
    class: "insp-kf" + (hasKeys(track) ? " active" : ""),
    dataset: { path: spec.path },
  }, "\u25C6");
  kfBtn.addEventListener("click", () => toggleKeyframe(c.id, spec.path, track));
  row.appendChild(box);
  row.appendChild(kfBtn);
  parent.appendChild(row);
}

async function applyValue(cid, path, value) {
  const c = clip(cid);
  if (!c) return;
  const track = getPath(c, path);
  // 乐观更新：拖滑块时立即反映到预览，不等服务端回包
  try {
    if (track && typeof track === "object" && "static" in track) {
      track.static = value;
    } else if (track !== undefined && (typeof track === "number" || typeof track === "boolean")) {
      // 裸值形态：就地改成 track
      let node = c;
      const parts = path.split(".");
      for (let i = 0; i < parts.length - 1; i++) node = node[parts[i]];
      node[parts[parts.length - 1]] = { static: value };
    }
    bus.emit("clip:changed", { clip: c, action: "optimistic" });
  } catch (e) {
    /* 忽略乐观更新失败 */
  }
  suppress = true;
  try {
    if (hasKeys(track)) {
      await addKeyframe(cid, path, localT(), value);
    } else {
      await patchClip(cid, { [path]: value });
    }
  } catch (e) {
    console.error("[inspector] 写入失败", path, e);
  } finally {
    suppress = false;
  }
}

async function toggleKeyframe(cid, path, track) {
  const t = localT();
  const cur = evalTrack(track, t, 0);
  suppress = true;
  try {
    if (hasKeys(track)) {
      const times = allTimes(track);
      const near = times.find((x) => Math.abs(x - t) < 1e-3);
      if (near != null) {
        await delKeyframe(cid, path, near);
      } else {
        await addKeyframe(cid, path, t, cur);
      }
    } else {
      await addKeyframe(cid, path, t, cur);
    }
  } catch (e) {
    console.error("[inspector] 关键帧操作失败", e);
  } finally {
    suppress = false;
    build();
  }
}

// ---------------------------------------------------------------- 视频 / 音频

function buildVideo(parent, c) {
  parent.appendChild(el("div", { class: "insp-section-title" }, "变换"));
  for (const spec of VIDEO_PARAMS) buildParamRow(parent, c, spec);
  // 擦除角度滑块（0~360）
  const angRow = el("div", { class: "insp-param", dataset: { path: "video.erode_angle" } });
  angRow.appendChild(el("label", { class: "insp-label" }, "擦除角度"));
  const angTrack = c.video.erode_angle;
  const angCur = evalTrack(angTrack, localT(), 0);
  const angRange = el("input", {
    type: "range", class: "insp-input", min: 0, max: 360, step: 1, value: angCur,
    dataset: { path: "video.erode_angle", kind: "range" },
  });
  const angNum = el("input", {
    type: "number", class: "insp-num", min: 0, max: 360, step: 1,
    value: Math.round(angCur),
    dataset: { path: "video.erode_angle", kind: "num" },
  });
  const angKf = el("button", {
    class: "insp-kf" + (hasKeys(angTrack) ? " active" : ""),
    dataset: { path: "video.erode_angle" },
    title: "关键帧",
  }, "\u25C6");
  angRange.addEventListener("input", () => {
    const v = clamp(parseFloat(angRange.value) || 0, 0, 360);
    angNum.value = Math.round(v);
    applyValue(c.id, "video.erode_angle", v);
  });
  angNum.addEventListener("change", () => {
    const v = clamp(parseFloat(angNum.value) || 0, 0, 360);
    angRange.value = v;
    applyValue(c.id, "video.erode_angle", v);
  });
  angKf.addEventListener("click", () => toggleKeyframe(c.id, "video.erode_angle", angTrack));
  angRow.appendChild(angRange);
  angRow.appendChild(angNum);
  angRow.appendChild(angKf);
  parent.appendChild(angRow);

  parent.appendChild(el("div", { class: "insp-section-title" }, "镜像"));
  for (const spec of VIDEO_BOOL) buildBoolRow(parent, c, spec);

  const en = el("input", { type: "checkbox", class: "insp-check" });
  en.checked = c.video.enabled !== false;
  en.addEventListener("change", () => applyValue(c.id, "video.enabled", en.checked));
  const row = el("div", { class: "insp-param" }, [
    el("label", { class: "insp-label" }, "启用视频"),
    en,
  ]);
  parent.appendChild(row);
}

function buildAudio(parent, c) {
  if (((c.media && c.media.kind) === "video")) {
    parent.appendChild(el("div", { class: "insp-note" },
      "此片段是视频素材，音频已禁用。\n需要声音请在素材箱用「音频卡」或点「分离」添加独立音轨。"));
  }
  for (const spec of AUDIO_PARAMS) buildParamRow(parent, c, spec);
  const en = el("input", { type: "checkbox", class: "insp-check" });
  en.checked = c.audio.enabled !== false;
  en.addEventListener("change", () => applyValue(c.id, "audio.enabled", en.checked));
  parent.appendChild(el("div", { class: "insp-param" }, [
    el("label", { class: "insp-label" }, "启用音频"),
    en,
  ]));
}

// ---------------------------------------------------------------- 效果（动态工具）

function buildEffects(parent, c) {
  // 已应用的效果实例
  const listWrap = el("div", { class: "insp-fx-list" });
  for (const fx of c.effects || []) {
    listWrap.appendChild(renderEffectInstance(c, fx));
  }
  parent.appendChild(listWrap);

  // 添加工具下拉
  const addWrap = el("div", { class: "insp-fx-add" });
  const sel = el("select", { class: "insp-select" });
  sel.appendChild(el("option", { value: "" }, "+ 添加效果"));
  const groups = {};
  for (const t of state.toolList || []) {
    if (t.disabled) continue;
    const g = (t.ui && t.ui.group) || t.category || "其他";
    if (!groups[g]) groups[g] = [];
    groups[g].push(t);
  }
  for (const [g, arr] of Object.entries(groups)) {
    const og = el("optgroup", { label: g });
    for (const t of arr) og.appendChild(el("option", { value: t.id }, t.name || t.id));
    sel.appendChild(og);
  }
  sel.addEventListener("change", async () => {
    const tid = sel.value;
    sel.value = "";
    if (!tid) return;
    suppress = true;
    try {
      await applyToolToClip(c.id, tid);
    } catch (e) {
      console.error("[inspector] 添加效果失败", e);
    } finally {
      suppress = false;
      build();
    }
  });
  addWrap.appendChild(sel);
  parent.appendChild(addWrap);
}

function renderEffectInstance(c, fx) {
  const tool = state.tools[fx.type] || {};
  const box = el("div", { class: "insp-fx", dataset: { iid: fx.id } });

  const head = el("div", { class: "insp-fx-head" });
  const en = el("input", { type: "checkbox", class: "insp-check" });
  en.checked = fx.enabled !== false;
  en.addEventListener("change", () => patchEffectEnabled(c.id, fx.id, en.checked));
  head.appendChild(en);
  head.appendChild(el("span", { class: "insp-fx-name" }, tool.name || fx.type));
  const del = el("button", { class: "insp-fx-del", title: "移除效果" }, "\u00D7");
  del.addEventListener("click", async () => {
    suppress = true;
    try {
      await removeToolInstance(c.id, fx.id);
    } finally {
      suppress = false;
      build();
    }
  });
  head.appendChild(del);
  box.appendChild(head);

  // 参数控件按 schema 生成
  const schema = tool.params || {};
  for (const [pname, spec] of Object.entries(schema)) {
    box.appendChild(renderEffectParam(c, fx, pname, spec));
  }
  return box;
}

function renderEffectParam(c, fx, pname, spec) {
  const path = `effects.${fx.id}.${pname}`;
  const track = (fx.params || {})[pname];
  const isKf = hasKeys(track);
  const type = spec.type || "number";

  if (type === "bool") {
    const row = el("div", { class: "insp-param" });
    row.appendChild(el("label", { class: "insp-label" }, spec.label || pname));
    const box = el("input", { type: "checkbox", class: "insp-check" });
    box.checked = !!evalTrack(track, localT(), spec.default);
    box.addEventListener("change", () => patchEffectParam(c.id, fx.id, pname, box.checked));
    row.appendChild(box);
    return row;
  }

  if (type === "select") {
    const row = el("div", { class: "insp-param" });
    row.appendChild(el("label", { class: "insp-label" }, spec.label || pname));
    const sel = el("select", { class: "insp-select" });
    for (const opt of spec.options || []) {
      const val = typeof opt === "object" ? opt.value : opt;
      const lab = typeof opt === "object" ? opt.label : opt;
      sel.appendChild(el("option", { value: val }, lab));
    }
    sel.value = evalTrack(track, localT(), spec.default);
    sel.addEventListener("change", () => patchEffectParam(c.id, fx.id, pname, sel.value));
    row.appendChild(sel);
    return row;
  }

  if (type === "color") {
    const row = el("div", { class: "insp-param" });
    row.appendChild(el("label", { class: "insp-label" }, spec.label || pname));
    const inp = el("input", { type: "color", class: "insp-color" });
    inp.value = evalTrack(track, localT(), spec.default || "#000000");
    inp.addEventListener("change", () => patchEffectParam(c.id, fx.id, pname, inp.value));
    row.appendChild(inp);
    return row;
  }

  // number / int
  const min = spec.min != null ? spec.min : 0;
  const max = spec.max != null ? spec.max : 100;
  const step = spec.step != null ? spec.step : (type === "int" ? 1 : 0.01);
  const cur = evalTrack(track, localT(), spec.default != null ? spec.default : 0);

  const row = el("div", { class: "insp-param", dataset: { path } });
  row.appendChild(el("label", { class: "insp-label" }, spec.label || pname));
  const range = el("input", { type: "range", class: "insp-input", min, max, step, value: cur });
  const num = el("input", { type: "number", class: "insp-num", min, max, step, value: round(cur, 3) });
  const onInput = (ev) => {
    const v = clamp(parseFloat(ev.target.value), min, max);
    if (Number.isNaN(v)) return;
    if (ev.target === range) num.value = round(v, 3);
    else range.value = v;
    patchEffectParam(c.id, fx.id, pname, v);
  };
  range.addEventListener("input", onInput);
  num.addEventListener("change", onInput);
  row.appendChild(range);
  row.appendChild(num);
  if (spec.unit) row.appendChild(el("span", { class: "insp-unit" }, spec.unit));

  if (spec.keyframeable !== false) {
    const kfBtn = el("button", { class: "insp-kf" + (isKf ? " active" : "") }, "\u25C6");
    kfBtn.addEventListener("click", () => toggleEffectKeyframe(c.id, fx.id, pname, track, cur));
    row.appendChild(kfBtn);
  }
  return row;
}

async function patchEffectEnabled(cid, iid, enabled) {
  suppress = true;
  try {
    await patchToolInstance(cid, iid, { enabled });
  } catch (e) {
    console.error("[inspector] 效果启停失败", e);
  } finally {
    suppress = false;
  }
}

async function patchEffectParam(cid, iid, pname, value) {
  const c = clip(cid);
  const fx = (c.effects || []).find((x) => x.id === iid);
  const track = fx && (fx.params || {})[pname];
  suppress = true;
  try {
    if (hasKeys(track)) {
      const path = `effects.${iid}.${pname}`;
      await addKeyframe(cid, path, localT(), value);
    } else {
      await patchToolInstance(cid, iid, { params: { [pname]: value } });
    }
  } catch (e) {
    console.error("[inspector] 效果参数失败", e);
  } finally {
    suppress = false;
  }
}

async function toggleEffectKeyframe(cid, iid, pname, track, cur) {
  const path = `effects.${iid}.${pname}`;
  const t = localT();
  suppress = true;
  try {
    if (hasKeys(track)) {
      const near = allTimes(track).find((x) => Math.abs(x - t) < 1e-3);
      if (near != null) await delKeyframe(cid, path, near);
      else await addKeyframe(cid, path, t, cur);
    } else {
      await addKeyframe(cid, path, t, cur);
    }
  } catch (e) {
    console.error("[inspector] 效果关键帧失败", e);
  } finally {
    suppress = false;
    build();
  }
}

// ---------------------------------------------------------------- 标记

function buildMarkers(parent, c) {
  const wrap = el("div", { class: "insp-markers" });
  const list = (c.markers || []).slice().sort((a, b) => (a.t || 0) - (b.t || 0));
  if (!list.length) {
    wrap.appendChild(el("div", { class: "insp-empty small" }, "暂无片段标记"));
  }
  for (const m of list) {
    const row = el("div", { class: "insp-marker" }, [
      el("span", { class: "insp-marker-dot", style: { background: m.color || "#ffcc00" } }),
      el("span", { class: "insp-marker-time" }, timecode(m.t || 0).slice(3, 11)),
      el("span", { class: "insp-marker-label" }, m.label || "标记"),
    ]);
    const del = el("button", { class: "insp-fx-del", title: "删除" }, "\u00D7");
    del.addEventListener("click", async () => {
      suppress = true;
      try {
        const { api } = await import("./api.js");
        const updated = await api.delClipMarker(state.pid, c.id, m.id);
        const { applyClip } = await import("./state.js");
        applyClip({ ...c, markers: updated }, "update");
      } finally {
        suppress = false;
        build();
      }
    });
    row.appendChild(del);
    wrap.appendChild(row);
  }

  const addBtn = el("button", { class: "insp-btn" }, "+ 在播放头处添加标记");
  addBtn.addEventListener("click", async () => {
    suppress = true;
    try {
      const { api } = await import("./api.js");
      const { applyClip } = await import("./state.js");
      const t = localT();
      const updated = await api.addClipMarker(state.pid, c.id, t, "标记 " + timecode(t).slice(3, 8));
      applyClip({ ...c, markers: updated }, "update");
    } finally {
      suppress = false;
      build();
    }
  });
  wrap.appendChild(addBtn);
  parent.appendChild(wrap);
}

// ---------------------------------------------------------------- 值刷新

function refreshValues() {
  const c = selectedClip();
  if (!c || !root) return;
  const body = root.querySelector(".insp-body");
  if (!body) return;
  const t = localT();

  body.querySelectorAll(".insp-param[data-path]").forEach((row) => {
    const path = row.dataset.path;
    const track = resolveTrack(c, path);
    if (track === undefined) return;
    const isKf = hasKeys(track);
    const range = row.querySelector(".insp-input");
    const num = row.querySelector(".insp-num");
    const kfBtn = row.querySelector(".insp-kf");
    const cur = evalTrack(track, t, 0);
    if (range && document.activeElement !== range) {
      range.value = Math.min(Math.max(cur, range.min), range.max);
    }
    if (num && document.activeElement !== num) num.value = round(cur, 3);
    if (kfBtn) {
      kfBtn.classList.toggle("active", isKf);
      if (isKf) {
        const near = allTimes(track).some((x) => Math.abs(x - t) < 1e-3);
        kfBtn.classList.toggle("at-key", near);
      } else {
        kfBtn.classList.remove("at-key");
      }
    }
  });
}


// __EOF__