# 大视频不播放 / 无声 —— 根因与修复

> 修复日期：2026-11-25
> 素材：`D:/a/out/mh/6.4.mp4` — 4.03 GB / 925.5 s / 1920×992 @59.94 / h264 + aac
> 影响文件：`core/media.py`、`api/routes_media.py`、`core/project.py`、
> `api/routes_clip.py`、`api/routes_assets.py`、`api/routes_project.py`、
> `static/js/preview.js`、`static/js/audio.js`、`static/js/api.js`、
> `static/js/settings_ui.js`

---

## 一、现象

导入 4.03 GB 的视频后：

- **画面黑屏**，时间轴有片段、播放头在走，画布不出图
- **完全无声**，音量条推满也没声音
- 素材箱缩略图正常，说明文件本身可读
- 30 秒的小视频一切正常

---

## 二、排查过程（证据链）

| 观测 | 命令 / 位置 | 结果 |
|---|---|---|
| 源编码是否兼容 | `probe()` | `browser_ok: true`（h264+aac，浏览器原生能解） |
| 代理文件是否存在 | `dir data/cache/proxy` | 存在，164 MB |
| 代理文件是否有效 | `probe(代理)` | **`ok: false, duration: null`** ← 不是有效 mp4 |
| 完成标记 | `os.path.isfile(p+'.meta')` | **不存在** |
| 文件是否还在写 | 3 秒两次采样 | 162 MB → 164 MB，**仍在增长** |
| 转码进程 | `Get-CimInstance Win32_Process` | **两个 ffmpeg 同时写同一个输出** |
| 两个进程 | PID 17788 / 10840 | 命令行**完全一致**，启动时间差 5 分钟 |
| moov 位置 | 读文件头尾 2MB | `moov in head: True` → 源文件本身是 faststart 布局 |

### 关键推论

**转码中的文件被当成了可用代理。**

`-movflags +faststart` 的含义是：转码全部结束后，把 `moov` atom（索引）**回填到文件头**。转码进行中，文件头是空的或只有 `ftyp`，浏览器根本解析不了。而旧代码的判定是：

```python
def proxy_state(path):
    p = proxy_path(path)
    if os.path.isfile(p):        # ← 只看文件存在！
        return {"ready": True, ...}
```

于是前端拿到半成品 → `<video>` 解码失败 → 黑屏；`decodeAudioData` 抛错 → 无声。

---

## 三、根因（五重叠加）

### ① 半成品暴露（直接原因）

`proxy_state()` 用 `os.path.isfile()` 判定就绪。转码中的 `.mp4` 文件存在，但**不是有效 mp4**（moov 未回填）。

### ② 完成标记从未参与判定

`ensure_proxy()` 成功后会写 `.meta`（`{"short_gop": true}`），这是唯一可靠的「转码完成」标志。但 `proxy_state()` **不读它**。写了等于没写。

### ③ 两个 ffmpeg 抢写同一文件

`_PROXY_JOBS` 是**进程内**字典，只能防同进程重复。重启服务后：

- 旧进程的 ffmpeg 变成**孤儿进程**继续跑（`launch.py` 只杀端口监听者，不杀 ffmpeg 子进程树）
- 新进程 `_PROXY_JOBS` 为空，又起一份
- 两个 ffmpeg 同时 `-y` 覆盖写同一个输出 → 文件被彻底写坏

这一点在采样时看得很清楚：文件大小在**持续增长**，但 `probe` 永远 `ok:false`。

### ④ 音频被迫下载整个 4GB 源

```js
const raw = await fetchArrayBuffer(path);   // 下载整个文件
const buf = await ac.decodeAudioData(raw);  // 全量解码
```

`fetchArrayBuffer` 走 `mediaUrl(path, true)` → 请求**视频代理**。视频代理要等 925 秒 1080p 转完（实测几十分钟）才可用，且即使可用，下载 164MB 再解码也不是好方案。

其实音频只需 `-vn` 抽一条 aac 轨 —— 源本身是 aac，`-c:a copy` 直接流复制，**0.3 秒出 36.9 MB**。

### ⑤ 体积阈值触发了无谓转码

配置里 `proxy.threshold_mb: 40`，4GB 远超阈值 → 自动转码。但这个源的编码**浏览器本来就能解**（`browser_ok: true`）。结果：白转 925 秒 1080p，几十分钟 CPU + 磁盘，还把文件写坏。

### ⑥ `LONG_THRESHOLD` 定义了但从未使用

`audio.js` 第 17 行：

```js
const LONG_THRESHOLD = 600;
```

全文件 **0 次引用**。说明「长音频走流式播放」的设计做了变量却漏了实现。925 秒 × 48kHz × 2ch × Float32(4B) ≈ **355 MB**，加上 `slice()` 复制翻倍到 ~700 MB，接近浏览器上限。

---

## 四、修复

### 1. 代理安全四条铁律（`core/media.py`）

**a) 先写 `.part`，成功后原子改名**

```python
tmp = out + ".part.mp4"
cmd += ["-movflags", "+faststart", tmp]
r = _run(cmd, timeout=3600)
if r["rc"] != 0 or not os.path.isfile(tmp):
    return {..., "status": "failed"}
os.replace(tmp, out)          # 原子替换，半成品永不暴露
_write_meta(out, {...})
```

`os.replace` 在同一分区内是原子操作。前端永远看不到 `.part` 文件。

**b) `proxy_state()` 必须「主文件 + `.meta`」同时存在**

```python
def _meta_ok(out, src=""):
    m = proxy_meta(out)
    if not (m.get("short_gop") or m.get("audio_only")):
        return False
    if src and m.get("source_size") is not None:
        if int(m["source_size"]) != os.path.getsize(src):
            return False      # 源文件变了 → 代理过期
    return True

def proxy_state(path, audio=None):
    p = proxy_audio_path(path) if use_audio else proxy_path(path)
    exists = os.path.isfile(p)
    ready = exists and _meta_ok(p, path)
    return {"ready": ready, ...}
```

顺带解决了「源文件被替换后代理仍是旧内容」的 stale 问题。

**c) `.lock` 文件锁防跨进程重复**

```python
def _acquire_lock(out):
    lp = out + ".lock"
    if os.path.isfile(lp) and time.time() - os.path.getmtime(lp) < 7200:
        return False                       # 2 小时内有效，视为在转
    os.remove(lp) if stale
    fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # 原子创建
    os.write(fd, f"{os.getpid()} {time}")
    os.close(fd)
    return True
```

`O_EXCL` 保证只有一个进程能创建成功。孤儿进程的锁会在 2 小时后过期，不会永久阻塞。

**d) 代理未就绪时 `/stream?proxy=1` 回退源文件**

```python
target = path
if C.arg("proxy") in ("1", "true", "yes"):
    st = media_mod.proxy_state(path, audio=True if want_audio else None)
    if st.get("ready"):
        target = st["path"]
# 未就绪 → target 保持源文件路径
```

大体积但编码兼容的源，浏览器能直接 Range 流播，不必等转码。

### 2. 音频专用代理（`ensure_audio_proxy`）

```python
def ensure_audio_proxy(path, force=False):
    info = probe(path)
    if not info.get("has_audio"):
        return {"ready": False, "status": "no-audio"}
    out = proxy_audio_path(path)          # <fp>.m4a
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vn"]
    acodec = str(info.get("audio_codec") or "").lower()
    if acodec in ("aac", "mp3"):
        cmd += ["-c:a", "copy"]           # 流复制，不重编码
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", tmp]
    ...
    os.replace(tmp, out)
```

- `-vn` 丢弃视频流
- 源是 aac/mp3 → `-c:a copy` 直接复制，**0.3 秒**
- 其他编码 → 转 aac 192k
- 输出 `<fp>.m4a`，4GB 视频对应 36.9 MB

接口：

```
POST /api/media/audio-proxy   {path}         生成并返回状态
GET  /api/media/proxy?path=&audio=1          查询音频代理状态
GET  /api/media/stream?path=&proxy=1&audio=1 取音频代理流（audio/mp4）
```

### 3. 转码策略进设置，默认用源文件

`project.settings.proxy_mode`，三档：

| 值 | 行为 |
|---|---|
| `off` | **默认**。用源文件流播，从不自动转码。最快、不占 CPU |
| `auto` | 仅当 `browser_ok=false`（hevc/mpeg4/wmv3）才转码 |
| `always` | 总是转码，大文件也转（慢） |

**后端**（`core/project.py`）：

```python
DEFAULT_SETTINGS = {
    ...
    "proxy_mode": "off",
}

def normalize(p):
    ...
    if str(p["settings"].get("proxy_mode") or "").lower() not in ("off", "auto", "always"):
        p["settings"]["proxy_mode"] = "off"
```

**校验**（`api/routes_project.py`）：

```python
_SETTINGS_ENUMS = {"proxy_mode": ("off", "auto", "always")}
# _validate_settings 里枚举优先于数值范围检查
```

**导入预热**（`routes_clip.py` / `routes_assets.py`）：

```python
_pm = str((ws.project.get("settings") or {}).get("proxy_mode") or "off")
if _pm == "always" or (_pm == "auto" and not info.get("browser_ok", True)):
    threading.Thread(target=_warm, daemon=True).start()
```

**前端取源**（`preview.js`）：

```js
function proxyMode() {
  const st = (state.project && state.project.settings) || {};
  return st.proxy_mode || "off";
}

// 视频元素
v.src = mediaUrl(path, proxyMode() !== "off");

// 解码失败分支
if (proxyMode() === "off") {
  bus.emit("toast", {
    msg: "此素材浏览器无法解码，请在设置里把「转码策略」改为「仅编码不兼容时」",
    kind: "error",
  });
  return;
}
// 否则才 watchProxy + requestProxy
```

`off` 模式下解码失败**只弹提示，不静默起 ffmpeg**。

**UI**（`settings_ui.js`）：设置对话框新增「转码策略」下拉，三档带说明。

### 4. 前端音频取流

```js
async function audioStreamUrl(path) {
  try {
    const st = await api.audioProxy(path);          // 后端抽出 .m4a
    if (st && st.ready) return { url: mediaUrl(path, true, true), proxy: true };
  } catch (e) {
    console.warn("[audio] 音频代理不可用，回退原文件", path, e);
  }
  return { url: mediaUrl(path, false), proxy: false };
}
```

`mediaUrl` 签名扩展为 `mediaUrl(path, proxy, audio)`。

---

## 五、验证结果

服务重启后（PID 11844，加载新代码）：

| 验证项 | 结果 |
|---|---|
| `GET /api/health` | `ok:true`、SocketIO 正常、ffmpeg 路径正确 |
| 项目 `settings.proxy_mode` | `off` ✅ |
| 轨道结构 | `V2 / V1 / SUB1(index 2) / A1` ✅ |
| 视频代理判定 | `ready:true, audio_only:false`（164 MB / 1394×720 / `.meta` 齐全） |
| **音频代理生成** | **`created ready:true size:36,892,219` 耗时 0.3 s** |
| `/stream`（源） | `video/mp4`，`4,228,969,755` 字节 |
| `/stream?proxy=1` | `video/mp4`，`164,933,137` 字节 |
| `/stream?proxy=1&audio=1` | `audio/mp4`，`36,892,219` 字节 |
| Range 请求 | `206` + `Content-Range: bytes 0-1023/4228969755` + `Accept-Ranges: bytes` ✅ |
| moov 位置 | **文件头**（faststart 布局）→ 浏览器可边下边播，不必下完 4 GB |
| 残留锁 / `.part` 文件 | 无 ✅ |

三条取源路径的长度关系正确：**源 4.03 GB → 视频代理 164 MB → 音频代理 36.9 MB**。

---

## 六、排障速查

遇到「黑屏 / 无声」按此顺序查：

**1. 编码是否兼容**

```
GET /api/media/playable?path=<源>
```
`browser_ok: false` → 编码问题，需要转码（设置改 `auto`）。

**2. 代理是否真的有效**

```
python -c "import core.media as m; print(m.probe('data/cache/proxy/<fp>.mp4'))"
```
`ok: false` 或 `duration: null` → 代理是半成品或损坏。

**3. 完成标记在不在**

```
python -c "import os; print(os.path.isfile('data/cache/proxy/<fp>.mp4.meta'))"
```
缺失 → 转码从未成功完成。

**4. 有没有孤儿 ffmpeg 抢写**

```
powershell Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" | Select ProcessId,CreationDate,CommandLine
```
同一输出文件有多个 ffmpeg → 立刻 `taskkill`，删掉损坏代理重来。

**5. 有没有卡死的锁**

```
dir data\cache\proxy\*.lock
```
存在且超过 2 小时会自动失效；要立刻重来就手工删。

**6. 前端看什么**

```js
window.__previewDebug.cache()   // ready / readyState / err
window.__audioDebug.state()     // ctxState / sources / processed 缓存键
```

**7. 音频独查**

```
POST /api/media/audio-proxy {"path": "<源>"}
```
`status: created` + 合理的 `size` → 音频链路通。

---

## 七、遗留问题

| 项 | 影响 | 说明 |
|---|---|---|
| 长音频内存占用 | 中 | >600 s 音频全量解码进内存约 355 MB（切片翻倍 ~700 MB）。`LONG_THRESHOLD=600` 已定义但**未启用**。彻底解法：>600 s 且 `speed=1/pitch=0` 时走 `<audio>` + `MediaElementSource` 流式播放（代价：该片段无法 WSOLA 变调） |
| 音频代理同步阻塞 | 低 | `POST /api/media/audio-proxy` 在请求线程里跑 ffmpeg。aac 源 0.3 s 无感，但非 aac 源（需重编码）可能几十秒。应改异步 + 轮询 |
| 孤儿 ffmpeg 未自动清理 | 低 | `launch.py` 只杀端口监听者，不杀 ffmpeg 子进程树。可加「启动时清理 `data/cache/proxy/*.part.*` + 残留 `.lock`」 |
| 视频代理无进度反馈 | 低 | 大文件转码时前端只能轮询 `ready`，没有百分比 |

---

## 八、教训

1. **「文件存在」不等于「文件可用」**
   凡是异步产出物，必须有显式的完成标记，且**所有消费方都以标记为准**。写标记但不读标记，等于没写。

2. **临时文件 + 原子改名是异步产出的唯一安全姿势**
   只要允许别人在转码中读文件，就一定会读到半成品。`.part` 后缀让半成品在命名空间层面就不可见。

3. **进程内锁管不住跨进程**
   `_PROXY_JOBS` 是字典，重启即失效。跨进程必须用文件系统锁（`O_EXCL`）或系统级互斥。

4. **默认值要选「代价最小」的那条路**
   默认自动转码 = 每个大文件都付几十分钟 CPU。默认用源文件 = 只有真正解不了的编码才付代价。用户能感知到卡顿时，让他自己去设置里开。

5. **抽音轨不需要转码**
   `-vn -c:a copy` 只做容器重封装，不做像素/采样级运算。4 GB → 36 MB / 0.3 s。之前把音频和视频绑在同一份代理上，是设计上的耦合错误。

<!-- __EOF__ -->