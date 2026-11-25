// 通用工具函数与轻量事件、DOM 辅助

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const isNum = (v) => typeof v === "number" && !Number.isNaN(v);
export const round = (v, d = 3) => {
  const m = Math.pow(10, d);
  return Math.round(v * m) / m;
};

let _uid = 0;
export const uid = (p = "c") => p + "_" + Date.now().toString(36) + (_uid++).toString(36);

export function clone(o) {
  return o == null ? o : JSON.parse(JSON.stringify(o));
}

// 秒 -> 尺规时间码 00:00:00.000
export function timecode(sec, fps = 60) {
  if (!isNum(sec) || sec < 0) sec = 0;
  const total = sec;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = Math.floor(total % 60);
  const ms = Math.floor((total - Math.floor(total)) * 1000);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms, 3)}`;
}

// 秒 -> 帧数
export function toFrames(sec, fps = 60) {
  return Math.round(sec * fps);
}
export function toSeconds(frames, fps = 60) {
  return frames / fps;
}

export function debounce(fn, wait = 80) {
  let t = null;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), wait);
  };
}

export function throttle(fn, wait = 16) {
  let last = 0;
  let timer = null;
  return function (...args) {
    const now = Date.now();
    const remain = wait - (now - last);
    if (remain <= 0) {
      clearTimeout(timer);
      timer = null;
      last = now;
      fn.apply(this, args);
    } else if (!timer) {
      timer = setTimeout(() => {
        last = Date.now();
        timer = null;
        fn.apply(this, args);
      }, remain);
    }
  };
}

// 轻量事件总线
export function makeBus() {
  const map = new Map();
  return {
    on(evt, fn) {
      if (!map.has(evt)) map.set(evt, new Set());
      map.get(evt).add(fn);
      return () => map.get(evt) && map.get(evt).delete(fn);
    },
    off(evt, fn) {
      const s = map.get(evt);
      if (s) s.delete(fn);
    },
    emit(evt, payload) {
      const s = map.get(evt);
      if (s) {
        for (const fn of Array.from(s)) {
          try {
            fn(payload);
          } catch (e) {
            console.error("[bus]", evt, e);
          }
        }
      }
      const all = map.get("*");
      if (all) {
        for (const fn of Array.from(all)) {
          try {
            fn({ event: evt, payload });
          } catch (e) {
            console.error("[bus*]", evt, e);
          }
        }
      }
    },
    clear() {
      map.clear();
    },
  };
}

// DOM 辅助
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset" && typeof v === "object") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== false && v != null) {
      node.setAttribute(k, v === true ? "" : v);
    }
  }
  const kids = Array.isArray(children) ? children : [children];
  for (const c of kids) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" || typeof c === "number"
      ? document.createTextNode(String(c))
      : c);
  }
  return node;
}

export function qs(sel, root = document) {
  return root.querySelector(sel);
}
export function qsa(sel, root = document) {
  return Array.from(root.querySelectorAll(sel));
}
export function clear(node) {
  if (node) while (node.firstChild) node.removeChild(node.firstChild);
}

export function fmtBytes(n) {
  if (!isNum(n)) return "-";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024;
    i++;
  }
  return x.toFixed(i ? 1 : 0) + u[i];
}

export function dbToLinear(db) {
  return Math.pow(10, db / 20);
}


// __EOF__