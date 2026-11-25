// 关键帧求值器（前端版）
// 【契约】必须与 core/keyframe.py 逐行等价：
// linear / hold / ease_in / ease_out / ease_in_out / bezier 六种缓动，
// 三次贝塞尔用二分 24 次求解，误差 < 1e-7。
// 不一致会导致预览与导出的画面产生偏差。

export const EASE_MODES = ["linear", "hold", "ease_in", "ease_out", "ease_in_out", "bezier"];
export const DEFAULT_BEZIER = [0.4, 0.0, 0.6, 1.0];

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

function bz(t, p1, p2) {
  const mt = 1 - t;
  return 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t;
}

function bzSolve(x, x1, x2) {
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) * 0.5;
    if (bz(mid, x1, x2) < x) lo = mid;
    else hi = mid;
  }
  return (lo + hi) * 0.5;
}

export function ease(mode, u, h) {
  u = clamp01(u);
  if (mode === "hold") return 0;
  if (mode === "ease_in") return u * u * u;
  if (mode === "ease_out") return 1 - Math.pow(1 - u, 3);
  if (mode === "ease_in_out") {
    return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
  }
  if (mode === "bezier") {
    const hh = Array.isArray(h) && h.length >= 4 ? h : DEFAULT_BEZIER;
    const x1 = +hh[0];
    const y1 = +hh[1];
    const x2 = +hh[2];
    const y2 = +hh[3];
    if (x1 <= 0 && x2 <= 0) return u;
    return bz(bzSolve(u, x1, x2), y1, y2);
  }
  return u;
}

export function isTrack(x) {
  return x && typeof x === "object" && !Array.isArray(x) && ("static" in x || "keys" in x);
}

export function hasKeys(x) {
  return isTrack(x) && Array.isArray(x.keys) && x.keys.length > 0;
}

export function asTrack(x) {
  return isTrack(x) ? x : { static: x };
}

// 求 t 秒（相对 clip 起点）的值
export function evalTrack(track, t, dflt = null) {
  if (track == null) return dflt;
  if (!isTrack(track)) return track;
  if ("static" in track) {
    const v = track.static;
    return v == null ? dflt : v;
  }
  let keys = track.keys || [];
  if (!keys.length) return dflt;
  if (typeof t !== "number" || Number.isNaN(t)) t = 0;
  if (keys.length === 1) return keys[0].v ?? dflt;
  keys = keys.slice().sort((a, b) => (+a.t || 0) - (+b.t || 0));
  if (t <= +keys[0].t) return keys[0].v ?? dflt;
  if (t >= +keys[keys.length - 1].t) return keys[keys.length - 1].v ?? dflt;
  let lo = 0;
  let hi = keys.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (+keys[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const k0 = keys[lo];
  const k1 = keys[hi];
  const span = +k1.t - +k0.t;
  if (span <= 0) return k1.v ?? dflt;
  const u = (t - +k0.t) / span;
  const f = ease(k0.e || "linear", u, k0.h);
  const v0 = k0.v;
  const v1 = k1.v;
  if (typeof v0 === "boolean" || typeof v1 === "boolean") {
    return u < 1 ? !!v0 : !!v1;
  }
  if (typeof v0 === "number" && typeof v1 === "number") {
    return v0 + (v1 - v0) * f;
  }
  return v0;
}

// 写入/覆盖一个关键帧，返回新 track（不改入参）
export function setKey(track, t, v, e = "linear", h = null) {
  let keys = [];
  if (!hasKeys(track)) {
    const base = isTrack(track) ? track.static : (track == null ? v : track);
    if (t > 1e-6 && base != null && base !== v) {
      keys.push({ t: 0, v: base, e: "linear" });
    }
  } else {
    keys = track.keys.map((k) => ({ ...k }));
  }
  e = EASE_MODES.includes(e) ? e : "linear";
  const kf = { t: Math.round(t * 1e6) / 1e6, v, e };
  if (e === "bezier") kf.h = h ? h.slice(0, 4) : DEFAULT_BEZIER.slice();
  let replaced = false;
  for (let i = 0; i < keys.length; i++) {
    if (Math.abs((+keys[i].t || 0) - t) < 1e-6) {
      keys[i] = kf;
      replaced = true;
      break;
    }
  }
  if (!replaced) keys.push(kf);
  keys.sort((a, b) => a.t - b.t);
  return { keys };
}

export function removeKey(track, t) {
  if (!hasKeys(track)) return asTrack(track);
  const keys = track.keys.filter((k) => Math.abs((+k.t || 0) - t) > 1e-6);
  if (!keys.length) return { static: null };
  if (keys.length === 1) return { static: keys[0].v };
  return { keys };
}

export function allTimes(track) {
  if (!hasKeys(track)) return [];
  return track.keys.map((k) => +k.t || 0).sort((a, b) => a - b);
}

// 判断两个 track 是否等价（用于避免无意义的重渲染）
export function sameTrack(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// 采样：给曲线绘制用
export function sample(track, t0, t1, n = 64) {
  const out = [];
  if (n < 2) n = 2;
  const step = (t1 - t0) / (n - 1);
  for (let i = 0; i < n; i++) {
    const t = t0 + i * step;
    out.push([t, evalTrack(track, t, 0)]);
  }
  return out;
}


// __EOF__