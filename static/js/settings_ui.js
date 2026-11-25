// 项目设置对话框：分辨率 / 帧率 / 采样率 / 背景色 / 画质预设
// 改动会直接写回 project.settings，影响预览画布尺寸和导出默认参数。

import { api } from "./api.js";
import { state, bus, refresh } from "./state.js";
import { el, clear } from "./util.js";

let modal = null;

// 常用分辨率预设
const RES_PRESETS = [
  { label: "1920×1080 (1080p)", w: 1920, h: 1080 },
  { label: "1280×720 (720p)", w: 1280, h: 720 },
  { label: "3840×2160 (4K)", w: 3840, h: 2160 },
  { label: "2560×1440 (2K)", w: 2560, h: 1440 },
  { label: "1080×1920 (竖屏)", w: 1080, h: 1920 },
  { label: "1080×1080 (方形)", w: 1080, h: 1080 },
  { label: "自定义", w: 0, h: 0 },
];

const FPS_PRESETS = [24, 25, 30, 50, 60, 120];

function ensure() {
  if (modal) return modal;
  modal = el("div", { class: "modal", id: "settings-modal" }, [
    el("div", { class: "modal-box settings-box" }, [
      el("div", { class: "modal-head" }, [
        el("span", { class: "modal-title" }, "项目设置"),
        el("button", { class: "modal-close", onclick: close }, "\u00D7"),
      ]),
      el("div", { class: "settings-body", id: "settings-body" }),
      el("div", { class: "modal-foot" }, [
        el("span", { class: "modal-info", id: "settings-info" }, ""),
        el("span", { class: "modal-spacer" }),
        el("button", { class: "btn", onclick: close }, "取消"),
        el("button", { class: "btn primary", id: "settings-save" }, "应用"),
      ]),
    ]),
  ]);
  document.body.appendChild(modal);
  modal.addEventListener("click", (ev) => {
    if (ev.target === modal) close();
  });
  return modal;
}

function open() {
  ensure().classList.add("open");
  render();
}

function close() {
  const m = document.getElementById("settings-modal");
  if (m) m.classList.remove("open");
}

function render() {
  const body = document.getElementById("settings-body");
  if (!body) return;
  clear(body);
  const st = (state.project && state.project.settings) || {};

  // ---- 分辨率
  const presetSel = el("select", { class: "insp-select", id: "set-preset" });
  let matched = RES_PRESETS.length - 1;
  RES_PRESETS.forEach((p, i) => {
    const o = el("option", { value: String(i) }, p.label);
    if (p.w === st.width && p.h === st.height) matched = i;
    presetSel.appendChild(o);
  });
  presetSel.value = String(matched);

  const wIn = el("input", { type: "number", class: "insp-num", id: "set-w",
    min: 160, max: 7680, step: 2, value: st.width || 1920 });
  const hIn = el("input", { type: "number", class: "insp-num", id: "set-h",
    min: 120, max: 4320, step: 2, value: st.height || 1080 });

  presetSel.addEventListener("change", () => {
    const p = RES_PRESETS[Number(presetSel.value)];
    if (p && p.w) {
      wIn.value = p.w;
      hIn.value = p.h;
    }
  });
  const syncPreset = () => {
    const w = Number(wIn.value);
    const h = Number(hIn.value);
    let idx = RES_PRESETS.length - 1;
    RES_PRESETS.forEach((p, i) => {
      if (p.w === w && p.h === h) idx = i;
    });
    presetSel.value = String(idx);
  };
  wIn.addEventListener("input", syncPreset);
  hIn.addEventListener("input", syncPreset);

  body.appendChild(row("分辨率预设", presetSel));
  const pair = el("div", { class: "settings-pair" }, [wIn, el("span", {}, "×"), hIn]);
  body.appendChild(row("画布尺寸", pair));

  // ---- 帧率
  const fpsSel = el("select", { class: "insp-select", id: "set-fps-mode" });
  for (const v of FPS_PRESETS) fpsSel.appendChild(el("option", { value: String(v) }, v + " fps"));
  fpsSel.appendChild(el("option", { value: "custom" }, "自定义"));
  const curFps = Number(st.fps) || 60;
  if (FPS_PRESETS.includes(curFps)) {
    fpsSel.value = String(curFps);
  } else {
    fpsSel.value = "custom";
  }
  const fpsIn = el("input", { type: "number", class: "insp-num", id: "set-fps",
    min: 1, max: 240, step: 1, value: curFps });
  fpsSel.addEventListener("change", () => {
    if (fpsSel.value !== "custom") fpsIn.value = fpsSel.value;
  });

  body.appendChild(row("帧率", fpsSel));
  body.appendChild(row("自定义帧率", fpsIn));

  // ---- 音频
  const srSel = el("select", { class: "insp-select", id: "set-sr" });
  for (const v of [44100, 48000, 96000]) {
    const o = el("option", { value: String(v) }, (v / 1000) + " kHz");
    if (Number(st.sample_rate) === v) o.selected = true;
    srSel.appendChild(o);
  }
  body.appendChild(row("采样率", srSel));

  // ---- 转码策略
  const PROXY_MODES = [
    ["off", "用源文件（不转码，推荐）"],
    ["auto", "仅编码不兼容时转码"],
    ["always", "总是转码（大文件也转，较慢）"],
  ];
  const proxySel = el("select", { class: "insp-select", id: "set-proxy-mode" });
  const curMode = st.proxy_mode || "off";
  for (const [v, label] of PROXY_MODES) {
    const o = el("option", { value: v }, label);
    if (v === curMode) o.selected = true;
    proxySel.appendChild(o);
  }
  body.appendChild(row("转码策略", proxySel));

  // ---- 背景
  const bgIn = el("input", { type: "color", class: "insp-color", id: "set-bg",
    value: st.background || "#000000" });
  body.appendChild(row("背景色", bgIn));

  body.appendChild(el("div", { class: "settings-hint" },
    "画布尺寸决定预览和导出的最终分辨率。\n" +
    "帧率影响导出编码与关键帧对齐（导出时源会被重采样到此帧率）。\n" +
    "改动立即生效，已有片段会按新画布重新适配（等比缩放留黑边）。\n" +
    "转码策略：默认用源文件流播，最快且不占 CPU。\n" +
    "仅当画面黑屏/卡顿且编码为 hevc/mpeg4/wmv3 时才需要转码。"));

  document.getElementById("settings-save").onclick = save;
  document.getElementById("settings-info").textContent = "改动会立即应用";
}

function row(label, ctrl) {
  return el("div", { class: "settings-row" }, [
    el("label", {}, label),
    ctrl,
  ]);
}

async function save() {
  const info = document.getElementById("settings-info");
  const patch = {
    "settings.width": Number(document.getElementById("set-w").value),
    "settings.height": Number(document.getElementById("set-h").value),
    "settings.fps": Number(document.getElementById("set-fps").value),
    "settings.sample_rate": Number(document.getElementById("set-sr").value),
    "settings.background": document.getElementById("set-bg").value,
    "settings.proxy_mode": document.getElementById("set-proxy-mode").value,
  };
  try {
    await api.patchProject(state.pid, patch);
    await refresh();
    bus.emit("project:loaded", state.project);
    bus.emit("toast", { msg: "项目设置已更新", kind: "ok" });
    close();
  } catch (e) {
    info.textContent = "应用失败：" + e.message;
    info.style.color = "var(--err)";
  }
}

export function init() {
  const btn = document.getElementById("btn-settings");
  if (btn) {
    btn.onclick = open;
    btn.title = "项目设置（分辨率/帧率/格式）";
  }
}

// ================================================================ 字幕播报
// 在预览上临时显示当前播放头字幕（供无预览时也能确认）
export function currentSubtitleAt(t) {
  const subs = (state.project && state.project.subtitles) || [];
  for (const s of subs) {
    if (t >= s.start - 1e-6 && t <= s.end + 1e-6) return s;
  }
  return null;
}

// __EOF__