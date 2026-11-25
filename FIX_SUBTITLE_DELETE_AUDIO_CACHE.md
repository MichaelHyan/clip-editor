# 字幕回格删除 + 音频本地缓存

> 修复日期：2026-11-25
> 影响文件：`static/js/main.js`、`static/js/audio.js`、
> `static/js/audio_cache.js`（新增）、`tools/browser_verify_sub_audio.py`（新增）

---

## 一、需求

1. **字幕轨道要能选中后按回格（Delete/Backspace）删除**
2. **音频解码结果做本地缓存**，避免每次刷新都重新下载 36 MB 音频代理

---

## 二、需求 1：字幕回格删除

### 问题

`main.js` 的 `deleteSelected()` 只处理 `state.selectedClipId`：

```js
async function deleteSelected() {
  const cid = S.state.selectedClipId;
  if (!cid) return;                 // ← 选中字幕时这里直接返回
  await safeRun(async () => {
    await api.delClip(S.state.pid, cid);
    ...
  });
}
```

字幕**不在 clip 体系里**（存在 `project.subtitles[]`，`selectedSubtitleId` 独立于
`selectedClipId`），所以选中字幕后按 Delete 完全没反应。

检查器里虽然有「删除此字幕」按钮，但需要鼠标点，且要先点进去。

### 修复

`deleteSelected()` 改为**字幕优先，再回落片段**：

```js
async function deleteSelected() {
  // 字幕优先：字幕不在 clip 体系里，走独立的删除接口
  const sid = S.state.selectedSubtitleId;
  if (sid) {
    await safeRun(async () => {
      await subtitles.removeSubtitle(sid);
      toast("已删除字幕", "ok");
    });
    return;
  }

  const cid = S.state.selectedClipId;
  if (!cid) return;
  await safeRun(async () => {
    await api.delClip(S.state.pid, cid);
    S.selectClip(null);
    await S.refresh();
    toast("已删除片段", "ok");
  });
}
```

**为什么字幕优先**：`selectSubtitle()` 会清 `selectedClipId`，`selectClip()`
会清 `selectedSubtitleId`，两者正常互斥。理论上不会同时有值，但**字幕优先**
是更安全的兜底——万一状态残留，用户看到选中着字幕块，按 Delete 就该删字幕。

`subtitles.removeSubtitle()` 内部已做三件事：

1. `DELETE /api/projects/<pid>/subtitles/<sid>`
2. 清 `state.selectedSubtitleId`
3. `refresh()` + 广播 `subtitle:changed` → 时间轴重渲染、检查器回到空状态

键位绑定无改动（`Delete` 和 `Backspace` 都已绑到 `deleteSelected`）。

### 验证

| 检查项 | 结果 |
|---|---|
| 点顶栏「字幕」后 `subtitles` +1 | ✅ |
| 新字幕自动被选中 | ✅ `sub_7b09017db9` |
| 时间轴出现 `.tl-sub` 且带 `.selected` | ✅ |
| 检查器切到「字幕」面板 | ✅ |
| **按 Delete → 字幕归 0、节点消失、选中清空** | ✅ |
| **按 Backspace 同样能删** | ✅ |

---

## 三、需求 2：音频解码本地缓存

### 问题

`audio.js` 的 `bufferCache` 是**内存 Map**（`cid -> AudioBuffer`），刷新页面即丢。
每次刷新后播放，都要重新走一遍：

```
fetch(音频代理 36 MB) → decodeAudioData(1~3s) → bufferCache
```

36 MB 每次重下，局域网无所谓，但源文件走 fallback 时可能下载数 GB。

### 方案选择

| 方案 | 缓存内容 | 大小 | 问题 |
|---|---|---|---|
| A. 缓存 PCM（AudioBuffer 的声道数据） | Float32Array | **~355 MB**（925s×48k×2ch×4B） | 撑爆配额，且 AudioBuffer 不能直接存 |
| **B. 缓存压缩字节（.m4a）** | **ArrayBuffer** | **~36 MB** | 命中后仍需 decodeAudioData |
| C. HTTP 缓存（`Cache-Control`） | 响应体 | 36 MB | 服务端目前是 `no-store`，且无法控制失效 |

**选 B**：36 MB 存得下，命中后只花 1~3 秒解码，省掉网络。PCM 存不下也没必要
——解码是 CPU 密集但很快，网络下载才是慢的那一环。

### 实现：`static/js/audio_cache.js`（新增）

IndexedDB 封装，keyPath 为 `key`：

```js
const DB_NAME = "clip-editor-audio";
const STORE = "audio";
export const MAX_ENTRY_BYTES = 256 * 1024 * 1024;    // 单条上限 256MB
const MAX_TOTAL_BYTES = 1024 * 1024 * 1024;          // 总量上限 1GB
```

**缓存键**（`audioCacheKey`）：

```js
export function audioCacheKey(media, path) {
  const probe = (media && media.probe) || {};
  const kind = (media && media.kind) || "video";
  const fp = probe.fingerprint;
  if (fp) return `${fp}:${kind}`;                    // 指纹优先
  const size = probe.size;
  if (size) return `${path}:${size}:${kind}`;        // 其次 path+size
  return `${path}:${kind}`;                          // 最后退回 path
}
```

**指纹优先的意义**：源文件被替换（同名不同内容）→ 指纹变 → 旧缓存自动不复用。
`path:size` 兜底覆盖"同路径同大小但内容不同"的罕见情况。

**导出的 API**：

| 函数 | 作用 |
|---|---|
| `audioCacheKey(media, path)` | 算缓存键 |
| `get(key)` | 读，未命中返回 `null` |
| `put(key, data)` | 写，超单条上限返回 `false` |
| `remove(key)` | 删单条 |
| `clear()` | 清空所有 |
| `stats()` | `{available, count, bytes, items}` |
| `prune()` | 超总量上限时按 `saved` 从旧到新删 |

`stats()` / `prune()` 给后续做「设置里显示缓存占用 + 手动清理」留的接口。

### 接入 `audio.js`

`decodeFull()` 改为三段式：

```js
const cacheKey = audioCache.audioCacheKey(c.media, path);
const p = (async () => {
  try {
    const ac = ensureCtx();
    let buf = null;

    // 1) 本地字节缓存命中：省掉网络下载（音频代理 .m4a 约 36MB）
    const cached = await audioCache.get(cacheKey);
    if (cached) {
      try {
        buf = await ac.decodeAudioData(cached);
      } catch (e) {
        console.warn("[audio] 缓存解码失败，重新下载", cid, e);
        audioCache.remove(cacheKey).catch(() => {});
        buf = null;
      }
    }

    // 2) 缓存未命中：下载 + 解码，并把压缩字节写回缓存
    if (!buf) {
      const raw = await fetchArrayBuffer(path);
      // decodeAudioData 会 detach 传入的 buffer，所以先复制一份存缓存。
      // 复制前判大小：源文件 fallback 时可能是 4GB，绝不能白复制。
      if (raw.byteLength <= audioCache.MAX_ENTRY_BYTES) {
        audioCache.put(cacheKey, raw.slice(0)).catch(() => {});
      }
      buf = await ac.decodeAudioData(raw);
    }

    bufferCache.set(cid, buf);
    return buf;
  } catch (e) { ... }
})();
```

### 三个必须注意的细节

**① `decodeAudioData` 会 detach 传入的 ArrayBuffer**

```js
ac.decodeAudioData(raw);        // 调用后 raw 变成 detached（byteLength = 0）
```

所以必须 `raw.slice(0)` 先复制再存。直接 `put(key, raw)` 会存进一个空 buffer。

**② 复制前先判大小**

`fetchArrayBuffer` 在音频代理不可用时会**回退源文件**，4 GB 源复制一份是灾难。
所以：

```js
if (raw.byteLength <= audioCache.MAX_ENTRY_BYTES) {
  audioCache.put(cacheKey, raw.slice(0)).catch(() => {});
}
```

**③ 缓存解码失败要能自愈**

缓存字节可能损坏（磁盘错误、写入中断）。`decodeAudioData(cached)` 抛错时：

```js
audioCache.remove(cacheKey).catch(() => {});   // 删掉坏缓存
buf = null;                                     // 走下载分支
```

不应该因为缓存坏了就永久播不出声。

### 调试接口

```js
window.__audioDebug.audioCacheStats()   // {available, count, bytes, items}
window.__audioDebug.clearAudioCache()   // 清空
```

### 验证

| 检查项 | 结果 |
|---|---|
| `audio_cache.js` 可从服务端加载（HTTP 200） | ✅ |
| `audioCacheKey` / `MAX_ENTRY_BYTES` 正确导出 | ✅ |
| IndexedDB 可打开（`stats().available`） | ✅ |
| `put` → `get` 命中且长度正确 | ✅ 8 字节 |
| `remove` 后读不到 | ✅ |
| 指纹键 = `abc123:audio` | ✅ |
| 无指纹兜底 = `D:/y.mp4:999:video` | ✅ |
| 浏览器控制台无错误 | ✅ |

---

## 四、两个需求共同暴露的设计问题

### 1. 删除动作要按「当前选中什么」分派

`deleteSelected()` 原来只认 clip。正确写法是按**选中状态的优先级**分派：

```js
if (selectedSubtitleId) → 删字幕
else if (selectedClipId) → 删片段
else → 什么都不做
```

新增其他可选中对象（标记、轨道）时，这里要同步扩展。**"删除"是全局动词，
但作用对象由选中状态决定**，不能只覆盖一种。

### 2. 缓存要分层：内存 vs 持久化

| 层 | 存活 | 存什么 | 成本 |
|---|---|---|---|
| `bufferCache`（Map） | 页面 | AudioBuffer（PCM） | 访问最快，占内存大 |
| `processedCache`（Map） | 页面 | WSOLA 后的 AudioBuffer | 同上 |
| `audioCache`（IndexedDB） | 浏览器 | 压缩字节（.m4a） | 访问略慢，占磁盘 |

**只缓存压缩形式**是关键判断。PCM 体积是压缩格式的 10 倍，塞进持久化缓存
既撑爆配额又没收益——解码本身只要 1~3 秒，重下载才是瓶颈。

---

## 五、遗留问题

| 项 | 影响 | 说明 |
|---|---|---|
| 缓存占用无 UI 展示 | 低 | `stats()` 已就绪，但设置对话框还没显示「音频缓存 36MB / 清空」按钮 |
| 缓存没有主动失效检查 | 低 | 目前靠 `fingerprint` 变化间接失效。「源文件 mtime 变了但大小和指纹都没变」的极端情况不会命中，但不影响正确性 |
| 长音频仍全量解码进内存 | 中 | >600s 音频 PCM 约 355MB（`LONG_THRESHOLD=600` 仍未启用）。缓存只解决了「网络慢」，没解决「内存大」 |
| 同一源多个 clip 各存一份 | 低 | 键含 `kind`（video/audio），同源的视频卡和音频卡会用不同键。但视频卡的 `audio.enabled` 强制 false，`scheduleAll` 会跳过，实际只有音频卡解码，不产生重复 |

---

## 六、复现验证命令

```bat
cd /d D:\git\clip-editor
python tools\browser_verify_sub_audio.py
```

输出（实测）：

```
== A. 字幕 Delete 删除 ==
  [OK] 初始字幕数为 0
  [OK] 点「字幕」后 +1  -> 1
  [OK] 新字幕被选中  -> sub_7b09017db9
  [OK] 时间轴出现 .tl-sub  -> 1
  [OK] .tl-sub 带 selected 类
  [OK] 检查器切到字幕面板  -> 字幕
  [OK] Delete 后字幕归 0
  [OK] selectedSubtitleId 已清空
  [OK] .tl-sub 节点消失
  [OK] Backspace 同样能删  -> mid=1 end=0

== B. 音频 IndexedDB 缓存 ==
  [OK] audio_cache.js 可加载
  [OK] put 成功
  [OK] get 命中且长度=8  -> 8
  [OK] IndexedDB 可用
  [OK] remove 后读不到
  [OK] 指纹键 = abc123:audio  -> abc123:audio
  [OK] 无指纹兜底 = path:size:kind  -> D:/y.mp4:999:video

== 控制台错误 ==
  无

== 汇总：17/17 通过 ==
```

<!-- __EOF__ -->