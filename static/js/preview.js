// Canvas 预览渲染
// 渲染流程（每帧）：
//   1. 清背景
//   2. 按轨道 index 从大到小（底层先画）遍历视频轨的片段
//   3. 命中播放头的片段：定位源 <video>/<img>，绘制到离屏 canvas，
//      依次应用 裁剪 -> 变换矩阵 -> 镜像 -> 透明度 -> 效果
//   4. 把离屏结果 drawImage 到主 canvas
// 效果分两类：css filter（ctx.filter）与 canvas op（手写像素处理）。

import { state, bus, tracks, clipsOfTrack, clipStart, clipDuration, resolveClipAt } from "./state.js";
import { api, mediaUrl } from "./api.js";
import { subtitleAt, drawSubtitle } from "./subtitles.js";
import { clamp } from "./util.js";

const CANVAS_W = 1920;
const CANVAS_H = 1080;

let canvas = null;
let ctx = null;
let off = null;
let offCtx = null;
let rafId = 0;
let dirty = true;
let lastFrameTime = 0;
let fpsCap = 60;

// cid -> { video, ready, lastSrcTime, kind }
const mediaCache = new Map();
// cid -> { pattern: CanvasPattern } 用于冻结的最后一帧
const freezeCache = new Map();

function ensureCanvas() {
  if (!canvas) {
    canvas = document.getElementById("preview-canvas");
    if (canvas) {
      ctx = canvas.getContext("2d", { alpha: false });
      off = document.createElement("canvas");
      off.width = CANVAS_W;
      off.height = CANVAS_H;
      offCtx = off.getContext("2d", { alpha: true });
    }
  }
  return !!canvas;
}

function resizeCanvasToProject() {
  if (!canvas || !state.project) return;
  const w = (state.project.settings && state.project.settings.width) || CANVAS_W;
  const h = (state.project.settings && state.project.settings.height) || CANVAS_H;
  canvas.width = w;
  canvas.height = h;
  if (off) {
    off.width = w;
    off.height = h;
  }
}

// ---------------------------------------------------------------- 媒体元素

// ---------------------------------------------------------------- 代理兜底
// 浏览器解不了的编码（mpeg4/hevc/wmv3…）必须转成 h264 才能预览。
// 策略：探测已知不兼容 -> 立即转码；未知 -> 等 <video> error 再转码。
// 转码就绪后只把 el.src 重新指向同一 URL（服务端会切到代理）并 el.load()。

const proxyBusy = new Set();      // 正在转码/轮询的 path
const proxyWaiters = new Map();   // path -> [callback]

function notifyProxyReady(path) {
  const list = proxyWaiters.get(path);
  if (list) {
    proxyWaiters.delete(path);
    for (const fn of list) {
      try {
        fn();
      } catch (e) {
        /* 忽略 */
      }
    }
  }
}

function watchProxy(path, cb) {
  if (!proxyWaiters.has(path)) proxyWaiters.set(path, []);
  proxyWaiters.get(path).push(cb);
}

async function requestProxy(path) {
  if (proxyBusy.has(path)) return;
  proxyBusy.add(path);
  bus.emit("proxy:busy", { path, busy: true });
  try {
    await api.buildProxy(path, { async: true });
  } catch (e) {
    console.warn("[preview] 请求转码失败", path, e);
  }
  // 轮询直到就绪或放弃（最长 10 分钟）
  const t0 = Date.now();
  const LIMIT = 600000;
  const tick = async () => {
    if (Date.now() - t0 > LIMIT) {
      proxyBusy.delete(path);
      bus.emit("proxy:busy", { path, busy: false, failed: true });
      return;
    }
    let st = null;
    try {
      st = await api.proxyState(path);
    } catch (e) {
      st = null;
    }
    if (st && st.ready) {
      proxyBusy.delete(path);
      bus.emit("proxy:busy", { path, busy: false, ready: true });
      notifyProxyReady(path);
      return;
    }
    setTimeout(tick, 1200);
  };
  setTimeout(tick, 1000);
}

function createVideoEl(path, entry) {
  const v = document.createElement("video");
  v.crossOrigin = "anonymous";
  v.preload = "auto";
  v.muted = true;
  v.playsInline = true;
  v.src = mediaUrl(path, true);
  entry.el = v;
  entry.ready = false;
  entry.failed = false;

  v.addEventListener("loadeddata", () => {
    entry.ready = true;
    entry.failed = false;
    markDirty();
  });

  v.addEventListener("error", () => {
    entry.ready = false;
    if (entry.failed) return;      // 已处理过，避免死循环
    entry.failed = true;
    // 解码失败：走代理。代理就绪后重载同一 URL
    watchProxy(path, () => {
      if (entry.el !== v) return;  // 期间已被替换
      entry.failed = false;        // 允许下一轮 error 再次触发
      v.src = mediaUrl(path, true) + "&v=" + Date.now();
      try {
        v.load();
      } catch (e) {
        /* 忽略 */
      }
    });
    requestProxy(path);  // 内部自带去重
  });
  return v;
}

function getMediaEl(cid) {
  const c = state.clips[cid];
  if (!c) return null;
  const path = c.media && c.media.path;
  const kind = (c.media && c.media.kind) || "video";
  if (!path) return null;

  let entry = mediaCache.get(cid);
  if (entry && entry.path === path) return entry;

  if (kind === "audio") return null;

  entry = { kind, path, el: null, ready: false, failed: false };
  mediaCache.set(cid, entry);

  if (kind === "image") {
    const img = new Image();
    img.decoding = "async";
    img.src = mediaUrl(path, true);
    entry.el = img;
    img.onload = () => {
      entry.ready = true;
      markDirty();
    };
    img.onerror = () => {
      entry.ready = false;
    };
    return entry;
  }

  // 编码兼容性判定：probe 里可能有 browser_ok（新数据），
  // 旧数据没有这个字段，需要向服务端查一次 /playable。
  const probe = (c.media && c.media.probe) || {};
  entry.el = createVideoEl(path, entry);

  if (probe.browser_ok === false) {
    requestProxy(path);          // 已知不兼容，立刻转码
  } else if (probe.browser_ok === undefined) {
    checkPlayable(path);         // 旧数据，异步查一次
  }
  return entry;
}

// 向 /playable 查询该源是否需要代理，缓存结果避免重复请求
const playableCache = new Map();

async function checkPlayable(path) {
  if (playableCache.has(path)) {
    const r = playableCache.get(path);
    if (r && r.needs_proxy) requestProxy(path);
    return;
  }
  playableCache.set(path, null);
  try {
    const r = await api.playable(path);
    playableCache.set(path, r);
    if (r && r.needs_proxy) {
      requestProxy(path);
      // 同时把判定回写到 clip 的 probe，减少后续请求
      for (const cid of Object.keys(state.clips)) {
        const c = state.clips[cid];
        if (c && c.media && c.media.path === path && c.media.probe) {
          c.media.probe.browser_ok = !!r.browser_ok;
          c.media.probe.browser_issues = r.issues || [];
        }
      }
    }
  } catch (e) {
    playableCache.set(path, false);
  }
}

function seekMedia(entry, srcTime) {
  if (!entry || !entry.ready) return;
  const el = entry.el;
  if (entry.kind === "image") return;
  if (Math.abs((el.currentTime || 0) - srcTime) > 0.06) {
    try {
      el.currentTime = Math.max(0, srcTime);
      entry.seeking = true;
      if (!entry._seekHook) {
        entry._seekHook = true;
        el.addEventListener("seeked", () => {
          entry.seeking = false;
          markDirty();
          // 解码器出帧有延迟，连续补几帧，避免停在黑帧
          let n = 0;
          const pump = () => {
            markDirty();
            if (++n < 6) setTimeout(pump, 60);
          };
          setTimeout(pump, 30);
        });
      }
    } catch (e) {
      /* 忽略 */
    }
  }
}

// 是否有任何视频仍在 seek 中（决定是否保持 dirty）
function anySeeking() {
  for (const [, entry] of mediaCache) {
    if (!entry || entry.kind === "image") continue;
    const el = entry.el;
    if (!el) continue;
    if (entry.seeking || el.seeking) return true;
  }
  return false;
}

// ---------------------------------------------------------------- 矩阵

// sw/sh 是**裁剪后**的源尺寸。必须用它算 dw/dh，
// 否则 drawImage 会把裁剪后的内容拉伸回原始尺寸（裁剪看起来像缩放）。
function applyTransform(c2d, rp, sw, sh, outW, outH) {
  const scale = rp.scale || 1;
  const [px, py] = rp.pos || [0, 0];
  const rot = ((rp.rotation || 0) * Math.PI) / 180;
  const [ax, ay] = rp.anchor || [0.5, 0.5];
  const fit = Math.min(outW / sw, outH / sh);
  const dw = sw * fit;
  const dh = sh * fit;
  c2d.translate(outW / 2, outH / 2);
  c2d.translate(px, py);
  c2d.rotate(rot);
  c2d.scale(scale, scale);
  c2d.translate(-(dw * (ax - 0.5)), -(dh * (ay - 0.5)));
  return { dw, dh };
}

// ---------------------------------------------------------------- 效果

function applyCssFilters(c2d, effects) {
  const parts = [];
  for (const fx of effects) {
    const t = fx.type;
    const p = fx.params || {};
    if (t === "blur" && p.radius) parts.push(`blur(${p.radius}px)`);
    else if (t === "brightness" && p.value != null) parts.push(`brightness(${1 + p.value})`);
    else if (t === "contrast" && p.value != null) parts.push(`contrast(${p.value})`);
    else if (t === "saturation" && p.value != null) parts.push(`saturate(${p.value})`);
    else if (t === "hue" && p.degrees != null) parts.push(`hue-rotate(${p.degrees}deg)`);
    else if (t === "grayscale" && p.amount != null) parts.push(`grayscale(${p.amount})`);
  }
  c2d.filter = parts.length ? parts.join(" ") : "none";
}

function applyCanvasOps(c2d, effects, w, h) {
  // 需要在像素级处理的效果，暂只支持晕影与颗粒的轻量近似
  let vignette = null;
  let grain = 0;
  for (const fx of effects) {
    const p = fx.params || {};
    if (fx.type === "vignette" && p.angle != null) vignette = p.angle;
    else if (fx.type === "film_grain" && p.intensity != null) grain = p.intensity;
  }
  if (vignette != null) {
    const g = c2d.createRadialGradient(
      w / 2, h / 2, Math.min(w, h) * (0.35 + vignette * 0.1),
      w / 2, h / 2, Math.max(w, h) * 0.75
    );
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, `rgba(0,0,0,${clamp(0.65, 0, 0.9)})`);
    c2d.fillStyle = g;
    c2d.fillRect(0, 0, w, h);
  }
  if (grain > 0) {
    drawGrain(c2d, w, h, grain);
  }
}

// 线性擦除（全角度）：沿角度方向从一侧擦掉画面。
// erode=0 不擦，erode=1 全擦，中间是渐变前沿。
// 用 destination-out + 线性渐变遮罩，比逐像素快几个数量级。
function applyErode(c2d, rp, w, h) {
  const erode = clamp(rp.video.erode || 0, 0, 1);
  if (erode <= 0.001) return;
  const soft = Math.max(0.01, clamp(rp.video.erodeSoftness || 0, 0, 1));
  const deg = ((rp.video.erodeAngle || 0) % 360 + 360) % 360;
  const rad = (deg * Math.PI) / 180;
  const cx = w / 2;
  const cy = h / 2;
  // 投影半径：覆盖画布在该方向上的半长
  const R = (Math.abs(w * Math.cos(rad)) + Math.abs(h * Math.sin(rad))) / 2;
  const dx = Math.cos(rad);
  const dy = Math.sin(rad);
  const x0 = cx - dx * R;
  const y0 = cy - dy * R;
  const x1 = cx + dx * R;
  const y1 = cy + dy * R;

  // 擦除前沿位置（归一化 coord 空间）
  // erode=0 -> front=-soft（全不透明）；erode=1 -> front=1（全透明）
  const front = erode * (1 + soft) - soft;
  const eraseAt = (c) => clamp((c - front) / soft, 0, 1);

  c2d.save();
  c2d.globalCompositeOperation = "destination-out";
  const g = c2d.createLinearGradient(x0, y0, x1, y1);
  const N = 12;
  for (let i = 0; i <= N; i++) {
    const c = i / N;
    g.addColorStop(c, `rgba(0,0,0,${eraseAt(c)})`);
  }
  c2d.fillStyle = g;
  c2d.fillRect(0, 0, w, h);
  c2d.restore();
}

function drawGrain(c2d, w, h, intensity) {
  // 简化的胶片颗粒：随机小点叠加，低频更新
  const count = Math.floor(w * h * 0.0008 * intensity);
  c2d.save();
  c2d.globalAlpha = clamp(intensity, 0, 1) * 0.35;
  c2d.fillStyle = "#fff";
  for (let i = 0; i < count; i++) {
    const x = Math.random() * w;
    const y = Math.random() * h;
    c2d.fillRect(x, y, 1, 1);
  }
  c2d.restore();
}

// ---------------------------------------------------------------- 单片段渲染

const dbg = { bail: {}, lastDrawn: null };

function bail(cid, reason) {
  dbg.bail[cid] = reason;
}

function renderClip(c2d, cid, absT, outW, outH) {
  const rp = resolveClipAt(cid, absT);
  if (!rp || !rp.video.enabled) return bail(cid, "no-resolve-or-disabled");
  if (rp.video.opacity <= 0.001) return bail(cid, "opacity-zero");

  const entry = getMediaEl(cid);
  if (!entry) return bail(cid, "no-entry");

  const srcIn = rp.video.srcIn || 0;
  const speed = rp.video.speed || 1;
  const localT = absT - clipStart(cid);
  const srcTime = srcIn + localT * speed;
  const srcOut = rp.video.srcOut;
  if (srcOut != null && srcTime > srcOut) {
    return bail(cid, "out-of-range:src=" + srcTime.toFixed(2) + ">out=" + srcOut.toFixed(2));
  }

  if (!entry.ready) {
    const el = entry.el;
    return bail(cid, "not-ready:kind=" + entry.kind
      + ":failed=" + !!entry.failed
      + ":readyState=" + (el ? el.readyState : "noel")
      + ":rsErr=" + (el && el.error ? el.error.code : 0)
      + ":netState=" + (el ? el.networkState : "-")
      + ":src=" + (el ? String(el.currentSrc || el.src || "").slice(-46) : ""));
  }
  seekMedia(entry, srcTime);

  const el = entry.el;
  const srcW = el.videoWidth || el.naturalWidth || 1280;
  const srcH = el.videoHeight || el.naturalHeight || 720;
  if (!srcW || !srcH) return bail(cid, "no-size");

  // 源画面裁剪（比例 0~1）
  const crop = rp.video.crop || { top: 0, bottom: 0, left: 0, right: 0 };
  const sx = srcW * clamp(crop.left, 0, 0.95);
  const sy = srcH * clamp(crop.top, 0, 0.95);
  const sw = srcW * clamp(1 - crop.left - crop.right, 0.05, 1);
  const sh = srcH * clamp(1 - crop.top - crop.bottom, 0.05, 1);

  offCtx.setTransform(1, 0, 0, 1, 0, 0);
  offCtx.clearRect(0, 0, outW, outH);
  offCtx.save();

  const [mx, my] = rp.video.mirror || [false, false];
  const { dw, dh } = applyTransform(offCtx, rp.video, sw, sh, outW, outH);
  if (mx) offCtx.scale(-1, 1);
  if (my) offCtx.scale(1, -1);

  applyCssFilters(offCtx, rp.effects);

  try {
    offCtx.drawImage(el, sx, sy, sw, sh, -dw / 2, -dh / 2, dw, dh);
    dbg.lastDrawn = { cid, absT, srcW, srcH, dw, dh };
    delete dbg.bail[cid];
  } catch (e) {
    bail(cid, "drawImage-threw:" + String(e).slice(0, 80));
  }
  offCtx.filter = "none";
  offCtx.restore();

  // 画布级效果（晕影 / 颗粒）
  offCtx.save();
  offCtx.setTransform(1, 0, 0, 1, 0, 0);
  applyCanvasOps(offCtx, rp.effects, outW, outH);
  offCtx.restore();

  // 线性擦除：用 destination-out 渐变遮罩，沿指定方向从一侧擦掉画面
  applyErode(offCtx, rp, outW, outH);

  // 合成到主画布
  c2d.save();
  c2d.globalAlpha = clamp(rp.video.opacity, 0, 1);
  c2d.drawImage(off, 0, 0, outW, outH);
  c2d.restore();
}

// ---------------------------------------------------------------- 主循环

export function renderFrame() {
  if (!ensureCanvas() || !ctx) return;
  if (!state.project) return;

  const outW = canvas.width;
  const outH = canvas.height;
  const absT = state.playhead;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = (state.project.settings && state.project.settings.background) || "#000";
  ctx.fillRect(0, 0, outW, outH);

  // 轨道 index 大 -> 底层；视频轨只有向前渲染，最后画 index 小的（上层）
  const vTracks = tracks().filter((t) => t.type === "video" && !t.hidden);
  vTracks.sort((a, b) => (b.index || 0) - (a.index || 0));

  for (const tr of vTracks) {
    const list = clipsOfTrack(tr.id);
    for (const c of list) {
      const s = clipStart(c.id);
      const e = s + clipDuration(c.id);
      if (absT < s - 1e-4 || absT > e + 1e-4) continue;
      renderClip(ctx, c.id, absT, outW, outH);
    }
  }

  // 字幕叠在最上层（不受片段变换影响）
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

function loop(now) {
  rafId = requestAnimationFrame(loop);
  if (anySeeking()) dirty = true;      // seek 期间持续重绘，seeked 后再来一帧
  const step = 1000 / fpsCap;
  if (!dirty && now - lastFrameTime < step) return;
  lastFrameTime = now;
  if (state.playing || dirty) renderFrame();
}

export function startRenderLoop() {
  if (rafId) return;
  ensureCanvas();
  resizeCanvasToProject();
  rafId = requestAnimationFrame(loop);
}

export function stopRenderLoop() {
  if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }
}

export function markDirty() {
  dirty = true;
}

// 播放时把 video 元素设成 playing，跟随播放头（省电模式）
export function syncPlaying(playing) {
  for (const [, entry] of mediaCache) {
    if (!entry || !entry.el || entry.kind === "image") continue;
    const el = entry.el;
    if (playing) {
      if (el.paused) {
        const p = el.play();
        if (p && p.catch) p.catch(() => {});
      }
    } else if (!el.paused) {
      try {
        el.pause();
      } catch (e) {
        /* 忽略 */
      }
    }
  }
  markDirty();
}

// ---------------------------------------------------------------- 初始化

export function init() {
  ensureCanvas();
  if (!canvas) {
    console.warn("[preview] 未找到 #preview-canvas");
    return;
  }
  resizeCanvasToProject();
  startRenderLoop();

  if (typeof window !== "undefined") {
    window.__previewDebug = {
      dbg,
      cache: () => [...mediaCache.entries()].map(([k, v]) => ({
        cid: k, kind: v.kind, ready: v.ready, failed: !!v.failed,
        readyState: v.el ? v.el.readyState : null,
        err: v.el && v.el.error ? v.el.error.code : null,
        src: v.el ? String(v.el.currentSrc || v.el.src || "").slice(-60) : null,
        w: v.el ? (v.el.videoWidth || 0) : 0,
      })),
      render: () => renderFrame(),
      state: () => ({ playhead: state.playhead, dirty, clips: Object.keys(state.clips) }),
      probeFrame: () => {
        const out = [];
        for (const [cid, entry] of mediaCache) {
          const rec = { cid, kind: entry.kind, ready: entry.ready,
                        readyState: entry.el ? entry.el.readyState : null,
                        seeking: entry.el ? entry.el.seeking : null,
                        ct: entry.el ? +(entry.el.currentTime || 0).toFixed(2) : null,
                        vw: entry.el ? entry.el.videoWidth : 0 };
          if (!entry.el) { rec.noel = true; out.push(rec); continue; }
          try {
            const cv = document.createElement("canvas");
            cv.width = 64;
            cv.height = 64;
            const g = cv.getContext("2d");
            g.drawImage(entry.el, 0, 0, 64, 64);
            const d = g.getImageData(0, 0, 64, 64).data;
            let nz = 0;
            for (let i = 0; i < d.length; i += 4) {
              if (d[i] > 8 || d[i + 1] > 8 || d[i + 2] > 8) nz++;
            }
            rec.videoPixels = nz;
          } catch (e) {
            rec.drawErr = String(e).slice(0, 80);
          }
          out.push(rec);
        }
        return out;
      },
    };
  }

  bus.on("playhead:changed", markDirty);
  bus.on("timeline:changed", markDirty);
  bus.on("clip:changed", markDirty);
  bus.on("view:changed", markDirty);
  bus.on("playing:changed", (v) => {
    syncPlaying(v);
    markDirty();
  });
  bus.on("project:loaded", () => {
    resizeCanvasToProject();
    markDirty();
  });
  bus.on("project:changed", () => {
    resizeCanvasToProject();
    markDirty();
  });
  // 播放中定期标记脏，保证视频帧刷新
  setInterval(() => {
    if (state.playing) markDirty();
  }, 1000 / 30);

  window.addEventListener("resize", markDirty);
}

// 获取某片段当前应显示的源时间（供 audio 同步）
export function sourceTimeFor(cid, absT) {
  const c = state.clips[cid];
  if (!c) return 0;
  const rp = resolveClipAt(cid, absT);
  if (!rp) return 0;
  const localT = absT - clipStart(cid);
  return localT * (rp.video.speed || 1);
}


// __EOF__