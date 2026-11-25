// 音频字节的本地缓存（IndexedDB）
// 目的：刷新页面后不必重新下载 + 重新解码整段音频。
//
// 缓存的是「压缩后的音频字节」（音频代理 .m4a，通常 36MB），
// 而不是 decodeAudioData 出来的 PCM（925s 双声道约 355MB，会撑爆配额）。
// 命中后仍需 decodeAudioData（1~3 秒），但省掉网络下载。
//
// 键的构成见 audioCacheKey()：优先用 probe.fingerprint（源文件指纹），
// 源被替换后指纹变化 → 自动不复用旧缓存。

const DB_NAME = "clip-editor-audio";
const DB_VERSION = 1;
const STORE = "audio";

export const MAX_ENTRY_BYTES = 256 * 1024 * 1024;    // 单条上限 256MB
const MAX_TOTAL_BYTES = 1024 * 1024 * 1024;   // 总量上限 1GB

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    let req = null;
    try {
      req = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (e) {
      resolve(null);
      return;
    }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: "key" });
        os.createIndex("saved", "saved");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
  return dbPromise;
}

// 缓存键：指纹优先，其次 path+size，最后退回 path
export function audioCacheKey(media, path) {
  const probe = (media && media.probe) || {};
  const kind = (media && media.kind) || "video";
  const fp = probe.fingerprint;
  if (fp) return `${fp}:${kind}`;
  const size = probe.size;
  if (size) return `${path}:${size}:${kind}`;
  return `${path}:${kind}`;
}

export async function get(key) {
  if (!key) return null;
  const db = await openDb();
  if (!db) return null;
  return new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readonly");
    } catch (e) {
      resolve(null);
      return;
    }
    const req = tx.objectStore(STORE).get(key);
    req.onsuccess = () => {
      const rec = req.result;
      if (rec && rec.data && rec.data.byteLength) resolve(rec.data);
      else resolve(null);
    };
    req.onerror = () => resolve(null);
  });
}

export async function put(key, data) {
  if (!key || !data || !data.byteLength) return false;
  if (data.byteLength > MAX_ENTRY_BYTES) return false;
  const db = await openDb();
  if (!db) return false;
  const ok = await new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch (e) {
      resolve(false);
      return;
    }
    const req = tx.objectStore(STORE).put({
      key,
      data,
      size: data.byteLength,
      saved: Date.now(),
    });
    req.onsuccess = () => resolve(true);
    req.onerror = () => resolve(false);
    tx.onabort = () => resolve(false);
  });
  if (ok) prune().catch(() => {});
  return ok;
}

export async function remove(key) {
  const db = await openDb();
  if (!db) return false;
  return new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch (e) {
      resolve(false);
      return;
    }
    const req = tx.objectStore(STORE).delete(key);
    req.onsuccess = () => resolve(true);
    req.onerror = () => resolve(false);
  });
}

export async function clear() {
  const db = await openDb();
  if (!db) return false;
  return new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch (e) {
      resolve(false);
      return;
    }
    const req = tx.objectStore(STORE).clear();
    req.onsuccess = () => resolve(true);
    req.onerror = () => resolve(false);
  });
}

export async function stats() {
  const db = await openDb();
  if (!db) return { available: false, count: 0, bytes: 0, items: [] };
  return new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readonly");
    } catch (e) {
      resolve({ available: false, count: 0, bytes: 0, items: [] });
      return;
    }
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => {
      const rows = req.result || [];
      let bytes = 0;
      const items = [];
      for (const r of rows) {
        const sz = r.size || (r.data ? r.data.byteLength : 0);
        bytes += sz;
        items.push({ key: r.key, size: sz, saved: r.saved });
      }
      items.sort((a, b) => (b.saved || 0) - (a.saved || 0));
      resolve({ available: true, count: rows.length, bytes, items });
    };
    req.onerror = () => resolve({ available: false, count: 0, bytes: 0, items: [] });
  });
}

// 超出总量上限时，按 saved 从旧到新删
export async function prune() {
  const st = await stats();
  if (!st.available || st.bytes <= MAX_TOTAL_BYTES) return 0;
  const db = await openDb();
  if (!db) return 0;
  const keep = st.items.slice();          // 已按 saved 降序
  let total = st.bytes;
  const doomed = [];
  while (total > MAX_TOTAL_BYTES && keep.length) {
    const victim = keep.pop();            // 取最旧的
    total -= victim.size || 0;
    doomed.push(victim.key);
  }
  if (!doomed.length) return 0;
  return new Promise((resolve) => {
    let tx = null;
    try {
      tx = db.transaction(STORE, "readwrite");
    } catch (e) {
      resolve(0);
      return;
    }
    const os = tx.objectStore(STORE);
    for (const k of doomed) os.delete(k);
    tx.oncomplete = () => resolve(doomed.length);
    tx.onerror = () => resolve(0);
    tx.onabort = () => resolve(0);
  });
}

// __EOF__