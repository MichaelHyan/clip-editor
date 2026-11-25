// 素材箱面板：导入素材、卡片渲染、拖拽上时间轴
// 设计：
//   - 导入 = 登记到素材箱（不占轨道），一个视频源展开成「视频卡 + 音频卡」
//   - 拖卡片到轨道 = 生成对应角色的 clip
//   - 卡片上的「+视频 / +音频」按钮用于不想拖拽时快速添加

import { api } from "./api.js";
import { state, bus, refresh } from "./state.js";
import { el, clear, clamp, timecode, debounce } from "./util.js";

let panel = null;
let bodyEl = null;
let countEl = null;
let cards = [];          // 当前卡片列表
let dragCard = null;
// 截取设置：asset_id -> {in, out, open}，只在内存维护，上时间轴时写进 clip
const assetTrim = new Map();

// ---------------------------------------------------------------- 初始化

export function init() {
  panel = document.getElementById("assets-panel");
  bodyEl = document.getElementById("assets");
  countEl = document.getElementById("assets-count");
  if (!panel || !bodyEl) {
    console.warn("[assets] 素材面板容器缺失");
    return;
  }

  const addBtn = document.getElementById("assets-add");
  if (addBtn) addBtn.addEventListener("click", requestImport);

  const collapseBtn = document.getElementById("assets-collapse");
  const mid = document.getElementById("mid");
  if (collapseBtn && mid) {
    collapseBtn.addEventListener("click", () => {
      mid.classList.toggle("assets-collapsed");
      // 折叠会改变预览区宽度，通知重算
      window.dispatchEvent(new Event("resize"));
    });
  }

  bus.on("project:loaded", () => loadAssets());
  bus.on("timeline:changed", () => {
    // 时间轴变化不影响素材，但刷新一下拖拽可用状态
  });

  loadAssets();
}

// 外部（main.js 导入完成后）调用
export function requestImport() {
  bus.emit("assets:request-import");
}

// ---------------------------------------------------------------- 数据

export async function loadAssets() {
  if (!state.pid) return;
  try {
    const d = await api.listAssets(state.pid);
    cards = d.cards || [];
    render();
  } catch (e) {
    console.warn("[assets] 加载素材失败", e);
  }
}

// 导入一个或多个文件路径到素材箱
export async function importPaths(paths) {
  if (!state.pid || !paths || !paths.length) return [];
  const added = [];
  for (const p of paths) {
    try {
      const res = await api.addAsset(state.pid, p);
      added.push(res);
    } catch (e) {
      console.error("[assets] 导入失败", p, e);
    }
  }
  await loadAssets();
  return added;
}

// ---------------------------------------------------------------- 渲染

function render() {
  if (!bodyEl) return;
  clear(bodyEl);
  if (countEl) countEl.textContent = String(cards.length);

  if (!cards.length) {
    bodyEl.appendChild(el("div", { class: "assets-empty" },
      "素材箱为空\n点击下方「导入素材」\n或把文件拖进窗口"));
    return;
  }

  for (const card of cards) {
    bodyEl.appendChild(renderCard(card));
  }
}

// ---------------------------------------------------------------- 截取区

function getTrim(card) {
  return assetTrim.get(card.asset_id) || { in: 0, out: Number(card.duration) || 0, open: false };
}

function setTrim(card, patch) {
  assetTrim.set(card.asset_id, { ...getTrim(card), ...patch });
}

function fmtSec(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "0";
  return String(Math.round(n * 1000) / 1000);
}

function renderTrim(card) {
  const t = getTrim(card);
  const dur = Number(card.duration) || 0;
  const active = t.in > 1e-3 || (dur > 0 && t.out > 1e-3 && t.out < dur - 1e-3);
  const wrap = el("div", { class: "asset-trim" + (t.open ? " open" : "") });

  const head = el("button", { class: "asset-trim-toggle" },
    (t.open ? "\u25BE " : "\u25B8 ") + "截取" +
    (active ? "  " + fmtSec(t.in) + "~" + fmtSec(t.out) : ""));
  head.addEventListener("click", (ev) => {
    ev.stopPropagation();
    setTrim(card, { open: !t.open });
    render();
  });
  wrap.appendChild(head);
  if (!t.open) return wrap;

  const mkRow = (label, key, fallback) => {
    const row = el("div", { class: "asset-trim-row" });
    row.appendChild(el("label", {}, label));
    const num = el("input", {
      type: "number", class: "asset-trim-num", min: 0, max: dur, step: 0.1,
      value: fmtSec(t[key] != null ? t[key] : fallback),
    });
    num.addEventListener("change", () => {
      let v = Math.max(0, Math.min(Number(num.value) || 0, dur));
      const cur = getTrim(card);
      if (key === "in") v = Math.min(v, (cur.out || dur) - 0.1);
      else v = Math.max((cur.in || 0) + 0.1, v);
      setTrim(card, { [key]: v });
      render();
    });
    row.appendChild(num);
    const btn = el("button", { class: "asset-trim-btn", title: "取当前播放头" }, "\u23F1");
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const cur = Math.max(0, Math.min(state.playhead, dur));
      const curT = getTrim(card);
      if (key === "in") setTrim(card, { in: Math.min(cur, (curT.out || dur) - 0.1) });
      else setTrim(card, { out: Math.max((curT.in || 0) + 0.1, cur) });
      render();
    });
    row.appendChild(btn);
    return row;
  };

  wrap.appendChild(mkRow("入点", "in", 0));
  wrap.appendChild(mkRow("出点", "out", dur));

  const len = Math.max(0, (t.out || dur) - (t.in || 0));
  wrap.appendChild(el("div", { class: "asset-trim-info" }, "长度 " + fmtSec(len) + "s"));

  const reset = el("button", { class: "asset-trim-btn wide" }, "重置");
  reset.addEventListener("click", (ev) => {
    ev.stopPropagation();
    setTrim(card, { in: 0, out: dur });
    render();
  });
  wrap.appendChild(reset);

  return wrap;
}

function roleClass(role) {
  if (role === "audio") return "audio";
  if (role === "image") return "image";
  return "video";
}

function roleLabel(role) {
  if (role === "audio") return "音频";
  if (role === "image") return "图片";
  return "视频";
}

function renderCard(card) {
  const node = el("div", {
    class: "asset-card",
    dataset: { asset: card.asset_id, role: card.role, path: card.path },
    draggable: "true",
    title: card.path,
  });

  // 缩略图区
  const thumb = el("div", { class: "asset-thumb" });
  if (card.role === "audio") {
    thumb.appendChild(el("div", { class: "audio-badge" }, [
      el("span", {}, "\u266B"),
      el("small", {}, card.has_audio ? "含音轨" : "音频"),
    ]));
  } else if (card.thumb) {
    const img = el("img", { src: card.thumb, alt: card.label, loading: "lazy" });
    img.addEventListener("error", () => {
      img.style.display = "none";
    });
    thumb.appendChild(img);
  }
  thumb.appendChild(el("span", { class: "asset-role " + roleClass(card.role) },
    roleLabel(card.role)));
  if (card.duration) {
    thumb.appendChild(el("span", { class: "asset-dur" }, timecode(card.duration).slice(3, 11)));
  }
  if (card.browser_ok === false) {
    thumb.appendChild(el("span", { class: "asset-warn", title: (card.browser_issues || []).join("；") },
      "需转码"));
  }
  node.appendChild(thumb);

  // 信息 + 操作
  const info = el("div", { class: "asset-info" });
  info.appendChild(el("div", { class: "asset-name" }, card.label || card.name));

  const metaBits = [];
  if (card.width && card.height) metaBits.push(card.width + "×" + card.height);
  if (card.fps) metaBits.push(card.fps.toFixed(2) + "fps");
  if (card.duration) metaBits.push(card.duration.toFixed(1) + "s");
  info.appendChild(el("div", { class: "asset-meta" }, metaBits.join("  ") || card.suffix || ""));

  info.appendChild(renderTrim(card));

  const actions = el("div", { class: "asset-actions" });

  const addBtn = el("button", {
    class: "asset-btn primary",
    title: "添加到时间轴",
    onclick: (ev) => {
      ev.stopPropagation();
      addToTimeline(card, {});
    },
  }, "+ 时间轴");
  actions.appendChild(addBtn);

  if (card.role === "video" && card.has_audio) {
    const splitBtn = el("button", {
      class: "asset-btn",
      title: "视频和音频分别放到两条轨道",
      onclick: (ev) => {
        ev.stopPropagation();
        addToTimeline(card, { split_av: true });
      },
    }, "分离");
    actions.appendChild(splitBtn);
  }

  const delBtn = el("button", {
    class: "asset-btn danger",
    title: "从素材箱移除（不删除源文件）",
    onclick: async (ev) => {
      ev.stopPropagation();
      const label = card.label || card.name || "此素材";
      if (!confirm("从素材箱移除「" + label + "」？\n（不会删除源文件，已放到时间轴的片段也不受影响）")) {
        return;
      }
      try {
        await api.removeAsset(state.pid, card.asset_id);
        await loadAssets();
      } catch (e) {
        console.error(e);
        bus.emit("toast", { msg: "移除失败：" + e.message, kind: "error" });
      }
    },
  }, "移除");
  actions.appendChild(delBtn);

  info.appendChild(actions);
  node.appendChild(info);

  // 拖拽
  node.addEventListener("dragstart", (ev) => {
    dragCard = card;
    node.classList.add("dragging");
    try {
      ev.dataTransfer.effectAllowed = "copy";
      ev.dataTransfer.setData("text/plain", JSON.stringify({
        asset_id: card.asset_id, role: card.role, path: card.path,
      }));
    } catch (e) {
      /* 忽略 */
    }
    bus.emit("assets:dragstart", card);
  });
  node.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    dragCard = null;
    bus.emit("assets:dragend", null);
  });
  node.addEventListener("dblclick", () => addToTimeline(card, {}));

  return node;
}

// ---------------------------------------------------------------- 上时间轴

export async function addToTimeline(card, opts) {
  if (!state.pid) return;
  try {
    const t = getTrim(card);
    const dur = Number(card.duration) || 0;
    const hasTrim = t.in > 1e-3 || (dur > 0 && t.out > 1e-3 && t.out < dur - 1e-3);
    const res = await api.assetToTimeline(state.pid, card.asset_id, {
      role: card.role,
      start: state.playhead,
      ...(hasTrim ? { src_in: t.in, src_out: t.out } : {}),
      ...(opts || {}),
    });
    await refresh();
    const made = res.clips || [];
    if (made.length) {
      // 选中第一个
      const { selectClip } = await import("./state.js");
      selectClip(made[0].id, { force: true, scrollIntoView: true });
    }
    bus.emit("toast", {
      msg: made.length > 1 ? "已添加视频 + 音频" : "已添加到时间轴",
      kind: "ok",
    });
  } catch (e) {
    bus.emit("toast", { msg: "添加失败：" + e.message, kind: "error" });
  }
}

// 供 timeline.js 处理 drop
export function currentDragCard() {
  return dragCard;
}

export function findByAsset(assetId, role) {
  return cards.find((c) => c.asset_id === assetId && c.role === role) || null;
}

// __EOF__