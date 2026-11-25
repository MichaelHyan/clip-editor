# clip-editor 技术文档

仿 Premiere 的本地视频剪辑系统。Flask + SocketIO 后端，原生 ESM 前端（零构建），渲染与导出走 ffmpeg。

- **项目根**：`D:/git/clip-editor`
- **服务地址**：`http://127.0.0.1:5173`
- **代码规模**：core 3840 + api 2188 = 6028 行，前端 JS 6343 行，CSS 1607 行，tools 4680 行（合计 18658 行 / 74 文件）

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器（原生 ESM，无构建）                                    │
│  ┌─────────┬──────────┬──────────┬─────────┬──────────────┐  │
│  │ 素材箱   │ Canvas   │ 时间轴    │ 检查器   │ 传输条        │  │
│  │ assets  │ preview  │ timeline │inspector│ transport    │  │
│  └────┬────┴────┬─────┴────┬─────┴────┬────┴──────┬───────┘  │
│       └─────────┴──────────┴──────────┘           │          │
│                    state.js（唯一状态镜像）        │          │
│              socket.js（实时同步 + 轮询降级）       │          │
└──────────────────────────┬──────────────────────────────────┘
                    REST + SocketIO
┌──────────────────────────┴──────────────────────────────────┐
│  Flask（run.py）                                             │
│  api/  routes_project / clip / history / media / effects     │
│        routes_agent / assets / export                        │
│  core/ workspace（内存缓存+并发锁）                           │
│        project / clip / keyframe / effects / assets           │
│        media / export / store / history / ops / realtime     │
└──────────────────────────┬──────────────────────────────────┘
                    磁盘（data/）
        projects/<pid>/  project.json + clips/*.json + assets.json
        effects/registry.json（agent 动态注册）
        cache/（代理/缩略图/波形/probe 缓存）
        exports/<jid>/（导出产物）
```

**核心设计原则**

1. **真相源在后端**：前端 `state.js` 只是镜像，所有变更经 API 落盘后回填
2. **片段独立成文件**：一片段一 JSON，天然可跨项目复用
3. **预览与导出共用求值器**：`core/keyframe.py` 与 `static/js/keyframe.js` 逐行等价
4. **乐观锁**：`rev` 字段，冲突返回 409 + 最新对象
5. **写操作统一入口**：`Workspace.lock` 内完成，UI 与 agent 并发安全

---

## 二、数据模型

### 三层结构

```
Project（项目/时间轴）
├── settings: 分辨率 / 帧率 / 采样率 / 背景色
├── tracks[]: Track（轨道）
│   ├── id / type(video|audio|subtitle|effect|marker) / index / muted / hidden / locked
│   └── clips[]: 只存 clip_id 引用（顺序无关，渲染按 timeline.start 排）
│                subtitle 轨不用 clips[]，字幕存 project.subtitles[]
│                effect/marker 为历史遗留类型，新建项目不再产生
├── markers[]: 时间轴标记
└── rev / playhead / shared_playhead
```

### 轨道增删（时间轴 UI）

`static/js/timeline.js`，后端复用已有的 `POST /tracks` / `DELETE /tracks/<tid>`。

| 位置 | 操作 |
|---|---|
| 工具栏「+ 视频轨」 | 加一条 video 轨，**置于最上层**（`index: -1`） |
| 工具栏「+ 音频轨」 | 加一条 audio 轨，**置于最下层**（`index: 9999`） |
| 轨道头「×」 | 删除该轨道（带片段会二次确认，字幕轨不给按钮） |
| 轨道名**双击** | 就地重命名（Enter 提交 / Esc 取消 / 失焦提交） |
| 轨道行**按住拖动** | 调整轨道顺序（插入线指示落点，松手落盘） |
| 轨道头「⇄」 | 切换 video ↔ audio（**仅空轨道**，字幕轨不给） |
| 轨道头**下边缘拖动** | 调整轨道高度（28~240px，双击恢复 64px） |

**命名规则**：`nextTrackName()` 取同类型轨道名末尾数字最大值 +1
（V2/V1 → V3，A1 → A2）。

**重命名的约束**：后端 `patch_track` 对 `name` 做 `strip()` 并截断到 40 字符，
空名被忽略（保留原名）。前端 `startRenameTrack()` 在 `pointerdown` 上
`stopPropagation()`，否则会同时触发整行的拖动排序。

**拖动排序的实现要点**：

- 用**插入线**（`.tl-head-insert`）指示落点，不做行位移动画。行高受
  `.tl-heads-inner` 的 `translateY(-scrollTop)` 影响，位移动画容易错位；
  插入线只需把指针 Y 和各行的 mid Y 比较，稳。
- 落点计算：`order.indexOf(tid)` 得到 `from`，插入线给的是「插到第 idx 位
  之前」，**移除自身后若目标在其后要左移一位**（`if (to > from) to -= 1`）。
  这一步忘掉就会差一位。
- `render()` 开头 `if (drag || headDrag) return`，拖拽期间禁止重建节点。
- `onHeadDown` 里排除 `button` 和 `input`，否则点按钮会启动拖动。

**轨道高度的实现要点**：

- 把手 `.tl-head-resize` 绝对定位在 `.tl-head` 内部（`bottom: 0`，高 5px）。
  **不能用 `bottom: -3px`**：`.tl-head` 是 `overflow: hidden`，负偏移会被裁掉
- 拖动中直接改 `row.style.height` 和 `lane.style.height`，并乐观写
  `tr.height`（让 `positionPlayhead()` 的 `laneTotalHeight()` 跟上），
  松手才 `patchTrack` 落盘，失败时 `render()` 还原
- `render()` 守卫要同时拦 `drag || headDrag || headResize`
- `onHeadResizeDown` 里 `ev.stopPropagation()`，否则会触发整行的拖动排序
- ⚠️ **`onHeadResizeUp` 在 `!moved` 分支里绝不能 `render()`**：
  双击 = down/up + down/up + dblclick，第一次 up 就重建节点会让后续
  `dblclick` 在已游离的旧节点上触发，监听器随之消失 → 双击恢复失效。
  这个坑实测才暴露（14/16 时双击恢复失败）。

**类型切换的三重约束**：

1. 仅 `video ↔ audio` 可互转。`subtitle` / `effect` / `marker` 不给按钮
2. **必须空轨道**。轨道上有片段时改类型会让片段语义与轨道不匹配
   （video 轨上挂 audio clip 等），前后端都拦
3. 切换后按新类型自动重命名（`nextTrackName(next)`，如 V1 → A2）

后端 `patch_track` 对 `type` 的校验：

```python
elif k == "type":
    t = str(v or "")
    if t not in project_mod.TRACK_TYPES:
        return C.err("非法轨道类型: " + t, 400)
    if t != tr.get("type") and (tr.get("clips") or []):
        return C.err("轨道上还有 %d 个片段，不能改类型" % len(tr["clips"]), 400)
    tr[k] = t
```

注意 `t != tr.get("type")` 这个判断：**同类型 patch 不算改类型**，不拦。
否则前端整行提交时带上不变的 type 也会被拒。

⚠️ **index 语义的坑**：后端 `add_track` 是

```python
p["tracks"].append(tr)
p["tracks"].sort(key=lambda t: t.get("index", 0))   # 稳定排序
for i, t in enumerate(...): t["index"] = i           # 重编号
```

新轨道 append 到末尾后按 index **稳定排序**再重编号。所以传 `index=0` 时，
原有那条 index=0 的轨（V2）会排在前面，新轨只能排第二。**要让视频轨真正
置顶必须传负数**（`-1`），音频轨传大数（`9999`）落到底部。

**字幕轨不提供删除按钮**：`normalize()` 会在下次读盘时自动补一条 SUB 轨
（`_ensure_subtitle_track()`），删了只会让人困惑，字幕数据也不会丢。

### 字幕（独立于 clip 体系）

字幕存在 `project.subtitles[]`，**不占用 clip**，由 `SUB` 轨统一管理：

```jsonc
{
  "id": "sub_xxx",
  "start": 1.0,          // 时间轴绝对时间（秒）
  "end": 4.0,
  "text": "字幕内容\n支持换行",
  "style": {
    "fontSize": 48,      // 按 1920 宽度归一化，实际渲染按画布宽度等比换算
    "color": "#ffffff",
    "outlineColor": "#000000",
    "outline": 3,        // 描边宽度（相对 font-size 48 的倍数）
    "x": 0.5,            // 0~1 归一化水平锚点
    "y": 0.86,           // 0~1 归一化垂直锚点
    "align": "center",   // left|center|right
    "bold": true
  }
}
```

**为什么独立于 clip**：字幕是全局资源，需要对整条时间轴统一管理（批量改样式、导出 SRT），
挂在某个 clip 上会导致跨片段字幕无法表达。预览时按播放头查 `subtitleAt(t)` 叠在最上层。

`data/projects/<pid>/` 结构：

```
project.json          项目 + 轨道 + 标记
clips_index.json      轻量索引（名称/类型/更新时间）
assets.json           素材箱登记（源文件引用）
clips/<cid>.json      片段完整对象
snapshots/            自动快照（保留 40 个）
```

### Clip 对象（核心）

```jsonc
{
  "id": "clip_xxx", "name": "主镜头", "rev": 3,
  "media": {
    "path": "D:/video/a.mp4", "kind": "video|audio|image",
    "probe": { /* ffprobe 结果 + browser_ok + browser_issues */ }
  },
  "timeline": { "track_id": "trk_v1", "start": 0.0, "duration": 10.0, "z": 0 },
  "video": {
    "enabled": true,
    "src_in": {"static": 0.0}, "src_out": {"static": null},   // null = 到源结尾
    "speed": {"static": 1.0},
    "scale": {"static": 1.0}, "pos_x": {...}, "pos_y": {...},
    "rotation": {...}, "anchor_x": {"static": 0.5}, "anchor_y": {...},
    "crop": {"top":..., "bottom":..., "left":..., "right":...},  // 比例 0~1
    "mirror_h": {...}, "mirror_v": {...},
    "erode": {"static": 0.0},                      // 线性擦除 0~1
    "erode_softness": {"static": 0.0},             // 羽化宽度
    "erode_dir": "left|right|top|bottom",          // 擦除方向（普通字段，非关键帧）
    "opacity": {"static": 1.0}, "blend": "normal"
  },
  "audio": {
    "enabled": true,
    "src_in": {...}, "src_out": {...}, "speed": {...},
    "pitch": {"static": 0.0},      // 半音 -24~24
    "gain": {"static": 0.0},       // dB
    "fade_in": {...}, "fade_out": {...}, "pan": {...}
  },
  "effects": [
    { "id": "fx_xxx", "type": "blur", "enabled": true,
      "params": {"radius": {"static": 2}}, "meta": {...} }
  ],
  "markers": [{"id":"mk_x","t":1.5,"label":"点","color":"#ffcc00"}],
  "meta": {"created":0,"updated":0,"author":"ui","tags":[],"notes":""}
}
```

**关键约束**：

- `kind=video` 的片段 **`audio.enabled` 强制 false**（`make_clip` 里设）。声音必须走独立音频片段，这样音量/音调/裁剪与画面解耦。
- `erode_dir` 是**普通字符串字段**，不是关键帧轨道。`normalize()` 里特判，否则会当 track 处理导致 `dvv["static"]` 崩溃。

### 参数三种形态

```jsonc
1.0                                          // 裸值，等同 static
{"static": 1.0}                              // 静态
{"keys":[{"t":0,"v":1,"e":"linear"}, {"t":2,"v":0.3,"e":"bezier","h":[0.4,0,0.6,1]}]}
```

- `t` 是**相对片段起点**的秒数
- `e` 缓动：`linear|hold|ease_in|ease_out|ease_in_out|bezier`
- `bezier` 用 `h: [x1,y1,x2,y2]`，缺省 `[0.4,0,0.6,1]`

---

## 三、素材箱（音视频分离的正确姿势）

**导入 ≠ 上时间轴**。两步：登记素材 → 从素材放到轨道。

`core/assets.py` + `api/routes_assets.py`。

一个含音轨的视频源登记后展开成**两张卡**：

| 卡片 | role | 拖到 | 生成 |
|---|---|---|---|
| 视频卡 | `video` | 视频轨 | `media.kind=video` 的 clip |
| 音频卡 | `audio` | 音频轨 | `media.kind=audio` 的 clip（**路径仍指向视频文件**，导出只取音轨） |

`split_av: true` 一次生成两个 clip，起点对齐：

```python
# 先在两条轨上找共同空位，保证音视频同步
common = find_free_start(vtrack, start, dur)
common = find_free_start(atrack, common, dur)
common = find_free_start(vtrack, common, dur)   # 回查防冲突
```

**截取导入**：`to-timeline` 支持 `{src_in, src_out}`，素材卡有折叠截取区（手输 / ⏱ 取播放头 / 重置）。

---

## 四、预览渲染管线（Canvas）

`static/js/preview.js`，逐帧渲染，动画循环 `requestAnimationFrame`。

### 渲染顺序

```
清背景 → 按轨道 index 降序遍历视频轨（底层先画）
  → 命中播放头的片段：
      取 <video>/<img> 元素 → seek 到源时间
      画到离屏 canvas：
        1. 裁剪（crop：比例裁源画面）
        2. 变换（applyTransform：fit 等比铺满 + scale + pos + rotation + anchor）
        3. 镜像（hflip/vflip）
        4. CSS filter（blur/brightness/contrast/saturation/hue/grayscale）
        5. 画布级效果（晕影/颗粒）
        6. 线性擦除（destination-out 渐变遮罩）
      合成到主 canvas（globalAlpha = opacity）
```

### 关键实现点

**`applyTransform` 必须用裁剪后的尺寸**：

```js
function applyTransform(c2d, rp, sw, sh, outW, outH) {
  const fit = Math.min(outW / sw, outH / sh);   // ← sw/sh 是裁剪后尺寸
  const dw = sw * fit, dh = sh * fit;
  ...
}
```

用原始 `srcW/srcH` 会导致裁剪内容被拉伸回原尺寸——看起来像缩放而非裁剪。验证：裁掉左右各 25% 后宽高比应从 16:9(1.78) 变 8:9(0.89)。

**线性擦除（erode，全角度）**：用 `destination-out` + 线性渐变遮罩，比逐像素快几个数量级。

擦除角度 `erode_angle` 是 0~360 度（0=从左往右，90=从上往下）。
预览按角度算投影方向：

```js
const rad = (deg * Math.PI) / 180;
const R = (Math.abs(w * Math.cos(rad)) + Math.abs(h * Math.sin(rad))) / 2;
const x0 = cx - Math.cos(rad) * R, y0 = cy - Math.sin(rad) * R;
// 渐变从 (x0,y0) 到 (cx,cy) 的延长线
```

导出端用 `geq` 改 alpha，同样按角度投影：

```python
coord = "(((X-W/2)*cos+(Y-H/2)*sin)/L+0.5)"   # L = W*|cos| + H*|sin|
alpha = f"clip(({coord}-{front})/{soft},0,1)*255"
```

```js
c2d.globalCompositeOperation = "destination-out";
const g = c2d.createLinearGradient(x0, y0, x1, y1);
for (let i = 0; i <= N; i++) {
  g.addColorStop(i/N, `rgba(0,0,0,${eraseAt(i/N)})`);
}
c2d.fillRect(0, 0, w, h);
```

擦除前沿：`front = erode * (1 + soft) - soft`。`erode=0` 时全不透明，`erode=1` 时全透明。

**视频元素代理兜底**：浏览器解不了的编码（mpeg4/hevc/wmv3）自动转码 H.264 代理并重载。见「编码兼容」。

**字幕叠在最上层**：`renderFrame` 末尾调 `drawSubtitle(ctx, sub, W, H)`，
不受片段变换影响。字号按画布宽度归一化（`fontSize / 1920 × canvasWidth`），
换分辨率时字幕视觉比例不变。

---

## 五、音频管线（WSOLA 变调/变速）

`static/js/audio.js` + `static/js/audio_process.js`。

### 为什么必须 WSOLA

Web Audio 的 `detune` 和 `playbackRate` **本质是同一个重采样倍率**：

```
净速率 = playbackRate × 2^(detune/1200)
```

设了 `detune` 必然改变速度。早期用 `playbackRate = speed / pitchRatio` 反向补偿，结果是**音高效果被完全抵消**。

### 解决方案

预处理成新 AudioBuffer，播放时 `rate` 固定 1.0：

| 操作 | 步骤 |
|---|---|
| 变调 N 半音 | ① 重采样到 `1/r` 长度（r = 2^(N/12)，音高变、时长变）<br>② WSOLA 拉伸回原长（时长恢复、音高保持） |
| 变速 S 倍 | WSOLA 拉伸到 `1/S` 长度（时长变、音高不变） |

WSOLA 参数（`audio_process.js`）：
- `FRAME = 2048`，汉宁窗
- `hopIn = FRAME/2`，`hopOut = hopIn × ratio`
- 相似度搜索半径 `hopIn × 0.25`

缓存键 `cid|speed|pitch`。**代价**：首次应用某组合有几百毫秒 CPU 延迟，之后走缓存。

### 三层缓存

| 层 | 存储 | 存活 | 内容 | 体积参考（925s 双声道 48k） |
|---|---|---|---|---|
| `bufferCache` | 内存 Map | 页面 | AudioBuffer（PCM） | ~355 MB |
| `processedCache` | 内存 Map | 页面 | WSOLA 后的 AudioBuffer | 同上 |
| `audioCache` | **IndexedDB** | 浏览器 | **压缩字节（.m4a）** | **~36 MB** |

**只持久化压缩形式**。PCM 是压缩格式的 10 倍，塞进 IndexedDB 既撑配额又
没收益 —— 解码本身 1~3 秒，重下载才是瓶颈。

`static/js/audio_cache.js` 的键用 `probe.fingerprint` 优先（源被替换自动失效），
`path:size:kind` 兜底。单条上限 256 MB，总量上限 1 GB，超了按 `saved` 从旧删。

⚠️ **`decodeAudioData` 会 detach 传入的 ArrayBuffer**，写缓存必须 `raw.slice(0)`
先复制；且复制前判 `byteLength <= MAX_ENTRY_BYTES`，否则源文件 fallback（4 GB）
时复制一次就是灾难。缓存解码失败要 `remove` 掉坏条目再走下载分支，不能永久失声。

调试：`window.__audioDebug.audioCacheStats()` / `clearAudioCache()`。

⚠️ 预览用简化 WSOLA，音质不如专业算法。**导出端用 ffmpeg `rubberband`** 才是高质量变调。

### 播放调度

- 音量/声像变化：`updateActiveParams()` 每 60ms 平滑更新（`setTargetAtTime`），不中断
- 音高/速度变化：`detectNeedsReschedule()` 检测到变化才重排

---

## 六、导出管线（ffmpeg filter_complex）

`core/export.py`。

### 构建流程

```
Graph(project, clips, opts)
  ├─ 预分配输入（每 clip 只 -i 一次，视频链与音频链共享）
  ├─ 视频链：trim → setpts → fit 缩放 → 用户 scale → 裁剪 → 镜像
  │          → 效果滤镜 → 格式转换 → 擦除(geq alpha)
  ├─ 音频链：atrim → asetpts → atempo → rubberband → volume
  │          → pan → afade → adelay
  └─ 合成：底层画布 → 按层 overlay（enable 时间窗）→ amix 音频
```

### 必须遵守的三条

**1. `build()` 必须幂等**

每次 build 前重置累积状态：

```python
def build(self):
    self.inputs = []; self.chains = []
    self.clip_input = {}; self._pos = {}; self.audio_parts = []
    self.warnings = []
    ...
```

否则 `preview-cmd` 先 build 再 command（内部又 build）→ 滤镜链重复两遍。

**2. 源必须等比缩放到画布**

```python
fit_expr = f"min({self.W}/iw,{self.H}/ih)"
parts.append(f"scale=w='trunc(iw*{fit_expr}/2)*2':h='trunc(ih*{fit_expr}/2)*2':flags=lanczos")
```

不缩放时 852×480 源会以原尺寸 overlay 到 1920×1080 中央，只占一小块。

**3. 擦除用 geq 改 alpha（0~255，不是 0~1）**

```python
alpha = f"clip(({coord}-{front})/{soft},0,1)*255"
parts.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'")
```
已实测确认 geq 的 a 范围是 0~255，乘 255 才对。

### 关键帧导出策略

| 参数 | 导出行为 |
|---|---|
| 位置 pos_x/pos_y、色相、响度 | ✅ 拟合成 `if(lt(t,...))` 时间表达式，动态生效 |
| 缩放、裁剪、透明度、模糊、倍速、音调、擦除 | ⚠️ 取片段起点值，写入 `warnings` |

需要最高保真就拆片段，每段静态值。

### 同轨防重叠

`Workspace.find_free_start(track_id, desired, dur, exclude_id)`：冲突就向右推到冲突片段结尾，循环到无冲突。**前端吸附只是体验层，后端四条路径全部接入**（导入/复制/移动/素材上轨）。

---

## 六点五、字幕系统

`static/js/subtitles.js` + `api/routes_project.py` 的字幕 CRUD。

### 界面

| 位置 | 功能 |
|---|---|
| 时间轴 SUB 轨 | 字幕块渲染，可拖动改时间、拖两边改时长、双击进编辑 |
| SUB 轨头 `+` 按钮 | 在播放头处加一条字幕（默认 3 秒） |
| 检查器 | 选中字幕时切换为字幕编辑页：内容/时间/样式/对齐/删除 |
| 时间轴选中字幕块后按 `Delete`/`Backspace` | 直接删除该字幕 |
| 预览画布 | 按播放头实时叠字 |

顶栏「字幕」按钮是**不依赖轨道类型**的兜底入口：在播放头处加一条字幕并
自动选中，随后在右侧检查器编辑内容。

### 接口

```
GET    /api/projects/<pid>/subtitles        列出全部
POST   /api/projects/<pid>/subtitles        新增 {start, end, text, style?}
PATCH  /api/projects/<pid>/subtitles/<sid>  修改（部分字段）
DELETE /api/projects/<pid>/subtitles/<sid>  删除
GET    /api/export/subtitles/<pid>/srt      导出 SRT 文件
```

样式字段走 `_norm_sub_style()` 规范化，数值钳制在合理范围（字号 8~400，
x/y 0~1，align 三选一）。

### 已知限制

**字幕目前不烧录进导出视频**，只支持导出 SRT 侧载文件。原因：烧录需要
`subtitles`/`ass` 滤镜 + 字体路径处理，且时机在 overlay 之后、编码之前，
与现有 filter_complex 结构需要额外拼接。这是下一轮要补的。

---

## 六点六、项目设置

顶栏「设置」按钮 → `settings_ui.js` 对话框。

| 项 | 范围 | 影响 |
|---|---|---|
| 画布宽高 | 160~7680 × 120~4320 | 预览画布尺寸、导出分辨率 |
| 帧率 | 1~240 fps | 导出编码帧率、关键帧对齐 |
| 采样率 | 8k/44.1k/48k/96k | 导出音频采样率 |
| 背景色 | 任意 | 画布底色（缩放留黑边时的边） |
| 转码策略 | off/auto/always | 是否把源转成 H.264 代理，见下 |

**后端校验**：`routes_project.py` 的 `_validate_settings()` 在
`PATCH /projects/<pid>` 前做范围检查，越界返回 400 并带具体数值。
枚举项（`proxy_mode`）另由 `_SETTINGS_ENUMS` 校验，非法值 400。

**转码策略 `settings.proxy_mode`**（默认 `off`）：

| 值 | 行为 |
|---|---|
| `off` | 用源文件流播，从不自动转码。**默认**，最快、不占 CPU |
| `auto` | 仅当编码浏览器不支持（`browser_ok=false`，如 hevc/mpeg4/wmv3）才转码 |
| `always` | 总是转码，大文件也转（慢） |

导入/素材上轨的自动预热、预览解码失败后的兜底转码，全部按此设置判定。
`off` 模式下解码失败只弹提示，不会静默起 ffmpeg。

**改动即时生效**：`project:changed` 事件触发前端 `fitPreview()` 重算画布，
不需要刷新页面。已有片段按新画布**等比缩放留黑边**（`export.py` 的 fit 缩放），
不会因为改分辨率被裁切。

---

## 七、效果/工具系统

`core/effects.py`，注册表模式。内置 10 个 + agent 动态注册。

```json
{
  "id": "film_grain", "name": "胶片颗粒",
  "category": "stylize", "scope": "video",
  "params": {
    "intensity": {"type":"number","default":0.3,"min":0,"max":1,"step":0.01,"keyframeable":true}
  },
  "preview": {"kind":"canvas","op":"grain"},
  "export":  {"filter":"noise","template":"noise=alls={intensity}:allf=t"},
  "ui": {"panel":"effects","group":"画面风格","order":10,"icon":"film","help":"动态噪点"}
}
```

**注册即生效**：`POST /api/agent/tools` → SocketIO 广播 `effect.registry.updated` → 前端效果面板立刻出现新控件，**无需重启、无需改前端代码**。

参数类型：`number|int|bool|select|color|text|vec2`。`select` 必须带 `options`。`keyframeable: true` 的参数前端生成关键帧按钮。

---

## 八、实时同步

`core/realtime.py` + `static/js/socket.js`。

所有写操作后广播：

| 事件 | 载荷 |
|---|---|
| `project.updated` / `project.patched` | 项目全量 / 增量 |
| `clip.updated` / `clip.patched` / `clip.removed` | 片段变更 |
| `effect.registry.updated` | 效果注册表变更（前端自动长控件） |
| `history.updated` | 撤销栈变化 |
| `playhead` | 播放头（共享播放模式） |
| `agent.event` | agent 操作通知（如 `export.progress`） |

- 自己的操作带 `client_id`，回环广播被忽略避免抖动
- SocketIO 不可用时**自动降级为 2s 轮询**

---

## 九、已知约束与坑（务必遵守）

### 1. 媒体路径信任模式

`config.json` 的 `media_trust_all: true`（默认）信任所有绝对路径。浏览接口自动枚举盘符。

收紧时改 `false`，但 `media_roots` 里的盘符根**必须写 `"D:/"`（带斜杠）**——写成 `"D:"` 会被 Windows 解析成"当前工作目录"，判定失效。

### 2. 浏览器编码兼容

浏览器只认 `h264/vp8/vp9/av1` + `aac/mp3/opus/vorbis/flac`。

- `probe()` 返回 `browser_ok` / `browser_issues`
- 是否转码由项目设置 `settings.proxy_mode` 决定（默认 `off` 用源文件）
- 转码产物写 `data/cache/proxy/<fp>.mp4`，**短 GOP**
  （`-g fps -keyint_min fps -sc_threshold 0`），否则拖动播放头大面积黑屏

**代理安全的四条铁律**（`core/media.py`）：

1. **先写 `.part.mp4`，成功后 `os.replace` 原子改名**。`-movflags +faststart`
   要等转码结束才回填 moov atom，转码中的文件不是有效 mp4
2. **`proxy_state()` 只认「主文件 + `.meta`」同时存在**。只看 `os.path.isfile()`
   会把半成品当可用代理 —— 曾经因此黑屏 + 无声
3. **`.lock` 文件锁**（`O_EXCL` 创建，2h 过期）。否则重启后孤儿 ffmpeg 和新进程
   会同时写同一个输出文件，把文件彻底写坏
4. **代理未就绪时 `/stream?proxy=1` 回退源文件**，绝不喂半成品给 `<video>`

**大文件音频专用代理**：`ensure_audio_proxy()` 用 `-vn` 只抽音轨成 `.m4a`
（源是 aac/mp3 时直接 `-c:a copy`，4GB 视频 0.3 秒出 36MB 音频）。
音频解码走这个文件，不必等整段视频转完，也不必下载 4GB 源。
接口：`POST /api/media/audio-proxy {path}`、`GET /stream?proxy=1&audio=1`。

**排查黑屏顺序**：
1. `GET /api/media/playable?path=` → `browser_ok: false` 就是编码问题
2. `ffmpeg -ss <t> -i <源> -frames:v 1 out.jpg` 抽帧 —— **确认不是素材本身黑场**
3. `window.__previewDebug.cache()` 看 `ready/readyState/err`
4. `window.__previewDebug.probeFrame()` 看 video 元素有无帧
5. `window.__previewDebug.dbg.bail` 看渲染在哪步提前返回

### 3. 裁剪语义

`POST /clips/<cid>/trim` 参数 `{src_in?, src_out?}`：
- 传 `null` 或不传 = 不动这一侧
- 左边缘裁剪只改 `src_in`，右边缘只改 `src_out`
- `src_out: null` 表示"到源结尾"，不会被固化

前端右边缘拖拽上限 = `min(源时长 probe.duration, 右邻居起点)`，下限取 `max(源开头, 左邻居终点)`。

### 4. 拖拽的三个坑

**a) `drag.moved` 不能只判水平位移**

跨轨拖拽常是纯垂直的（dx=0），只判 `Math.abs(dx) > 2` 会被当成"没动"。

```js
if (Math.abs(dx) > 2 || Math.abs(dy) > 2) drag.moved = true;
```

**b) 拖拽开始时不能被 render 重建节点**

`selectClip` 触发同步 `render()` → `drag.node` 变游离节点 → `pointer-events:none` 失效 → `elementFromPoint` 命中自身。

正确顺序：先设 `drag` + 加 `dragging-clip` 类 → 再 `selectClip` → `render()` 开头 `if (drag) return`。

**c) 轨道命中用坐标匹配，不用 elementFromPoint**

`laneAtY(clientY, wantType)` 遍历所有同类型 lane，取包含鼠标 Y 的那个（容差 40px）。避免浮层/播放头/关键帧点挡住。

### 5. 前端状态同步

`state.clips[cid]` 和 `state.project.tracks[].clips` 是**两份数据**：

- `state.clips` = 片段内容
- `state.project.tracks[].clips` = **轨道归属**（渲染读这个）

`applyClip()` 自动调 `syncTrackMembership(cid, oldTrack, newTrack)`。跨轨只更新前者会导致新轨道画面不显示。

### 6. Flask 模板缓存

`debug=False` 时 Flask 缓存 Jinja 模板，改 HTML 不生效。`run.py` 已显式关闭：

```python
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
```

**改 JS/CSS 不用重启，改 HTML 必须重启。**

### 7. 波形绘制

- 只有 `media.kind === "audio"` 的片段画波形，视频片段不画
- 必须按 `[src_in, src_out]` 裁剪：`fromIdx = srcIn/srcDur × n`，否则裁完片段波形还是整条的

### 8. 轨道头滚动

`.tl-heads` 是 `overflow:hidden`，内容不溢出时 `scrollTop` 恒为 0。用 `transform: translateY(-scrollTop)` 作用于内层 `.tl-heads-inner`。

### 9. 倍速必须重算时长

`duration_of()` 见到 `timeline.duration` 非空就直接返回。`patch_clip` 检测到 `speed/src_in/src_out` 变化时用 `source_duration()`（忽略缓存）重算。

### 10. flex 容器里的卡片会被压缩

`.assets-body` 是 `display:flex; flex-direction:column`，`.asset-card` 作为
flex item 默认 `flex-shrink:1`。素材一多，总高超过容器就会被**均匀压缩**，
最先消失的是信息区（名称/截取/按钮）——现象是"只看到缩略图，没有按钮"。

必须显式 `flex-shrink: 0`，让容器滚动而不是压缩内容。

### 11. 写文件要带哨兵

写入通道偶尔在文件末尾追加非 ASCII 残片。所有文件末尾带哨兵：
- Python: `# __EOF__`
- JS: `// __EOF__`
- CSS: `/* __EOF__ */`
- Markdown/HTML: `<!-- __EOF__ -->`

**绝对禁止** `open(p,'w').write(open(p).read())` —— `open(p,'w')` 先截断，再读就空了（曾清空 19 个文件）。正确写法先读入内存再写。

---

## 十、本轮修改记录

### 1. 导出尺寸差太大

**现象**：852×480 源导入 1920×1080 项目，导出画面只占中央一小块。

**根因**（三重叠加）：
- `Graph.build()` 往 `self.chains` 追加不重置 → 滤镜链重复两遍
- 源没有缩放到画布，直接以原尺寸 overlay 到画布中央
- 擦除 `erode` 只有数据字段，预览和导出都未实现

**修复**：
- `build()` 幂等化（每次重置累积状态）
- 加 `scale=w='trunc(iw*min(W/iw,H/ih)/2)*2':...` 等比铺满画布
- 实现 `erode_dir` 字段 + getq alpha 擦除 + 预览渐变遮罩

**验证**：852×480 源导出后画面填充率 99.8%；`erode=0.5` 导出后宽度精确减半（0.52）。

### 2. 素材区删除入口

原来只有一个小 `×` 按钮，容易误点也不显眼。改为显式「移除」文字按钮 + 确认框（说明不删源文件、不影响已上轨片段）。

### 3. 时间轴缩放限制

`clamp(pxPerSec, 4, 2000)` → `clamp(0.5, 20000)`。范围从 8px/s ~ 2k px/s 扩展到 0.5px/s（看整段长视频）~ 20000px/s（逐帧精修）。

### 4. 线性擦除方向

新增 `erode_dir` 字段（`left|right|top|bottom`），检查器加下拉选择。擦除以指定方向从一侧向另一侧推进，`erode_softness` 控制羽化宽度。

---

### 5. 素材箱不可见

**现象**：素材卡只显示缩略图，名称/截取/移除按钮全部不可见，用户只能靠选中+回格删。

**根因**：`.assets-body` 是 flex column，`.asset-card` 默认 `flex-shrink:1`，
9 张卡总高 1584px 超过容器 778px 就被均匀压缩到 78px，信息区被挤没。

**修复**：`.asset-card { flex-shrink: 0 }`，容器改为滚动。

**验证**：卡片高度 78px → 185px，「移除」按钮文字可见。

### 6. 线性擦除改全角度

原来只有 `erode_dir`（四个固定方向）。改为 `erode_angle`（0~360 度）：

- 预览：按角度算投影方向，`createLinearGradient` 从一侧到另一侧
- 导出：`geq` 用 `((X-W/2)*cos+(Y-H/2)*sin)/L+0.5` 算归一化投影坐标
- 检查器：角度滑块 + 数值 + 关键帧按钮

保留 `erode_dir` 字段以兼容旧数据（不再使用）。

### 7. 项目设置入口

顶栏「设置」按钮 → 对话框。可改画布宽高、帧率、采样率、背景色。
后端 `_validate_settings()` 做范围校验，越界 400。

改动即时生效，已有片段按新画布等比缩放留黑边。

### 8. 字幕系统

FX1（effect 轨）实际没用上，改为 SUB1 字幕轨。字幕独立存
`project.subtitles[]`，不占 clip。

- 时间轴：字幕块可拖拽改时间/时长，双击进编辑
- SUB 轨头 `+` 按钮：在播放头加字幕
- 检查器：完整字幕编辑（内容/时间/字号/颜色/描边/位置/对齐/删除）
- 预览：按播放头实时叠字，字号按画布宽度归一化
- 导出：支持 SRT 侧载文件（**烧录进视频尚未实现**）

### 9. 字幕入口不可见

**现象**：老项目主界面完全看不到字幕创建入口，`project.subtitles` 永远是 `[]`。

**根因**：字幕入口只挂在 `type == "subtitle"` 的轨道上（时间轴 SUB 轨头的「+」、
字幕块渲染），而老项目那条轨是早期 `effect` 类型的 FX1 占位轨。`make_project()`
新建项目建 SUB1，但 `normalize()` 不补，老项目就没有 subtitle 轨。

**修复**：

- `normalize()` 加幂等迁移 `_ensure_subtitle_track()`：优先把**空的 effect 轨
  就地改型**为 SUB1（保留 `track_id`，避免历史引用失效），否则在最后一条
  video 轨后插一条
- 顶栏加**永远可见**的「字幕」按钮（`#btn-subtitle`），不依赖轨道类型
- `selectClip()` 清 `selectedSubtitleId`，避免检查器卡在字幕编辑页

**教训**：结构性入口不能绑死在一个可变的、会被历史数据污染的状态上。
`normalize()` 是所有读写路径的必经之地，是最合适的幂等迁移点。

### 10. 大视频不播放 / 无声

**现象**：4GB 的 1920×992@59.94 h264+aac 视频导入后，画面黑屏、音频无声。

**根因**（四重叠加）：

- 体积超 40MB 阈值触发视频代理转码，但要转 925 秒 1080p（几十分钟）
- `proxy_state()` 只看文件存在就判定 `ready`，把**转码中的半成品**喂给前端。
  `-movflags +faststart` 要等转码结束才回填 moov，半成品不是有效 mp4
- 重启后旧进程的 ffmpeg 变孤儿，新进程 `_PROXY_JOBS` 为空又起一份，
  **两个 ffmpeg 抢写同一个文件** → 文件彻底写坏
- 音频走 `fetch(mediaUrl(path,true))` 下载整个 4GB 代理再 `decodeAudioData`，
  内存峰值约 700MB，且拿到的还是半成品

**修复**：

- 代理写 `.part.mp4` → 成功才 `os.replace` 原子改名
- `proxy_state()` 必须「主文件 + `.meta`」同时存在才算 ready
- `.lock` 文件锁防跨进程重复转码
- 新增 `ensure_audio_proxy()`：`-vn` 抽音轨，aac 源直接 copy（0.3s 出 36MB）
- `/stream?proxy=1` 代理未就绪回退源文件
- **转码策略进项目设置 `proxy_mode`，默认 `off` 用源文件**

**验证**：源文件 moov 在头部（faststart 布局），Range 请求返回 206，
浏览器可边下边播；音频代理 36.9MB / 0.3s；`/stream` 三条路径
（源 / 视频代理 / 音频代理）的 Content-Type 与长度均正确。

### 11. 字幕无法用回格删除

**现象**：时间轴上选中字幕块，按 `Delete`/`Backspace` 无反应，只能去检查器
点「删除此字幕」。

**根因**：`main.js` 的 `deleteSelected()` 只读 `state.selectedClipId`，读到空就
`return`。字幕存在 `project.subtitles[]`，选中状态是独立的 `selectedSubtitleId`，
不在 clip 体系里，所以永远走不到删除。

**修复**：`deleteSelected()` 改为**字幕优先**分派：

```js
if (state.selectedSubtitleId) → subtitles.removeSubtitle(sid)
else if (state.selectedClipId) → api.delClip(cid)
```

**教训**：「删除」是全局动词，作用对象由选中状态决定。新增可选中对象
（标记、轨道）时这里要同步扩展，别只覆盖一种。

### 12. 音频每次刷新都重新下载

**现象**：刷新页面后播放，音频都要重新拉一遍（音频代理 36 MB），源 fallback
时可能拉数 GB。

**根因**：`bufferCache` / `processedCache` 都是内存 Map，刷新即丢。

**修复**：新增 `static/js/audio_cache.js`，用 IndexedDB 缓存**压缩字节**
（音频代理 `.m4a`，约 36 MB），命中后省掉网络下载，只花 1~3 秒
`decodeAudioData`。键用 `probe.fingerprint`，源被替换自动失效。

**验证**（`tools/browser_verify_sub_audio.py`，Playwright + 系统 Edge）：
17/17 通过，覆盖字幕新增→选中→Delete/Backspace 删除全链路、IndexedDB
`put/get/remove/stats` 与缓存键格式，控制台无错误。

### 13. 轨道无法自由增删

**现象**：只能使用新建项目时的默认 4 条轨（V2/V1/SUB1/A1），无法加轨。

**根因**：后端 `POST /tracks`、`DELETE /tracks/<tid>` 和 `api.js` 封装
早就齐备，**缺的只是时间轴上的 UI 入口**。

**修复**：工具栏加「+ 视频轨」「+ 音频轨」，轨道头加「×」。
`HEAD_W` 148 → 172，容纳 4 个按钮不挤掉轨道名。

详见「轨道增删（时间轴 UI）」章节，尤其 index 语义那个坑。

### 14. 删除轨道后撤销无法恢复片段

**现象**：删除一条带片段的轨道，按 Ctrl+Z 后轨道回来了，但片段永远消失。

**根因**：`delete_track` 先 `ws.drop_clip(cid)` 把片段文件从磁盘删了，
但 `ws.commit()` 的 undo 列表**只记了 project 状态**：

```python
for cid in list(tr.get("clips") or []):
    ws.drop_clip(cid)                      # 片段文件已删
ws.commit("删除轨道",
          [{"target": "project", "state": before}],   # 只记 project
          ...)
```

撤销时 `apply_states` 只恢复 project，轨道回来了但 `clip_id` 指向已删除的
文件 → 前端 `clip(cid)` 返回 null → 轨道显示为空。**删轨道永久不可逆。**

**修复**：drop 之前先把每个片段的完整快照塞进 undo 列表，redo 里对应
`state: None`，并逐个 `emit_clip_removed` 让其他客户端同步：

```python
undo_states = [{"target": "project", "state": before}]
redo_states = []
for cid in clip_ids:
    c = ws.clips.get(cid)
    if c is not None:
        undo_states.append({"target": "clip", "id": cid,
                            "state": copy.deepcopy(c)})
    redo_states.append({"target": "clip", "id": cid, "state": None})
    ws.drop_clip(cid)
```

**教训**：任何「先删数据、后记录撤销」的操作都是错的。撤销快照必须在
**破坏性动作之前**采集。验证脚本 `tools/verify_delete_track_undo.py`
专测这条链路（9/9 通过）。

### 15. 浏览器测试破坏真实数据

**现象**：`browser_verify_track_ui.py` 直接在 `proj_00c13582b005` 上跑，
删掉了用户的 V1 轨和视频片段。

**根因**：测试脚本读 `localStorage` 里的项目，即用户当时正在编辑的那个。
破坏性测试（删轨、删片段）直接作用在线上数据上。

**修复**：测试自己 `POST /projects` 建临时项目，`goto('/?p=<temp_pid>')`
显式指定，二次校验 `state.pid === temp_pid` 不一致就中止，结束时
`DELETE` 掉。全程不碰现有项目。

**教训**：**任何破坏性自动化测试都必须自带隔离环境**。临时项目、临时
目录、临时端口都行，就是不能用「用户当前正在用的」。

### 16. 轨道重命名 / 拖动排序 / 类型切换

**现象**：轨道只能增删，不能改名、不能调顺序、不能换类型。

**根因**：后端 `PATCH /tracks/<tid>`（支持 `name`/`type`/`index`）和
`POST /tracks/reorder` 早就齐备，`api.js` 缺 `patchTrack` 封装，
前端零入口。

**修复**：

- `api.js` 加 `patchTrack(pid, tid, patch)`
- 轨道名双击 → 就地 `input`，Enter/Esc/失焦三态
- 轨道行按住拖动 → 插入线指示，落盘走 `POST /tracks/reorder`
- 轨道头加「⇄」按钮 → `video ↔ audio`，仅空轨可用
- `HEAD_W` 172 → 196，容纳 5 个按钮（M/H/L/⇄/×）
- 后端 `patch_track` 补 `type` 合法性校验 + 空轨校验 + `name` 截断

**详见**「轨道增删（时间轴 UI）」章节的「重命名/拖动/类型切换」小节。

**验证**：`tools/verify_track_rename_reorder_type.py`（新增，API + 浏览器
两段，全程临时项目）**23/23 通过**，覆盖重命名（空名/超长/Esc）、类型切换
（空轨可改/非空被拒/非法类型 400/同类型不拒）、排序（生效/index 连续/
不完整 order 不丢轨/指定轨排第一）、拖动真实鼠标轨迹、控制台无错误。

### 17. 轨道高度不可调

**现象**：轨道行高固定 64px，片段密集时看不清。

**根因**：后端 `patch_track` 早就支持 `height`（钳制 28~240），轨道头和
lane 也都按 `tr.height` 渲染，**缺的只是拖拽把手**。

**修复**：每行底部加 `.tl-head-resize`（5px 高，`cursor: ns-resize`），
拖动改高度，双击恢复 64px。

**踩到的坑**：双击恢复一开始失效。`onHeadResizeUp` 在「没移动」分支里
也调了 `render()`，而双击是 down/up + down/up + dblclick —— 第一次 up
就重建节点，后续 dblclick 落在已游离的旧节点上，监听器已消失。
修复：`!moved` 时只清理，不重建（没移动本来就什么都没变）。

**验证**：`tools/verify_track_height.py`（新增）**16/16 通过**，覆盖把手
存在、向下/向上拖动、lane 同步、落盘与刷新持久、28/240 极值钳制、
双击恢复、拖动把手不误触发排序、控制台无错误。

---

## 十一、当前已知问题

| 项 | 状态 | 说明 |
|---|---|---|
| 工具注销后已挂载实例变孤儿 | 未修 | Inspector 无 schema，只显示原始值不显示滑块（数据不丢）。理想是注销改软禁用 |
| 关键帧只能打点 | 未修 | 没有曲线拖拽编辑 |
| 导出同轨重叠 | 已修 | 后端 `find_free_start` 兜底，但 UI 拖拽可能仍产生视觉重叠（数据层已对齐） |
| 旧片段音视频绑定 | 数据遗留 | 之前按旧方式导入的片段音视频在同 clip 内，不受新约束影响。需重新导入拆分 |
| 字幕不烧录进视频 | **待实现** | 目前只导出 SRT 侧载。烧录需 `subtitles`/`ass` 滤镜 + 字体路径，下一轮补 |
| 字幕无法跨项目复用 | 未实现 | 字幕存在项目级 `subtitles[]`，没有像 clip 那样的片段库 |
| WSOLA 音质 | 固有限制 | 简化实现，导出端用 rubberband 更佳 |
| 字幕轨被手工删除 | 自动恢复 | 删掉 SUB1 后，下次读盘 `normalize()` 会重新补一条（字幕数据不丢，只是轨道重建） |
| 音频缓存无 UI 展示 | 未实现 | `audio_cache.stats()` 已就绪，设置对话框还没做「音频缓存 X MB / 清空」入口，目前只能用 `window.__audioDebug` |
| 缓存失效只看指纹 | 可接受 | 「源 mtime 变了但大小和指纹都没变」的极端情况会命中旧缓存，不影响正确性 |
| 长音频仍全量解码 | 待优化 | 唯一会在真实大文件上出问题的一项。>600s 音频全量解码进内存约 355 MB（切片翻倍约 700 MB），`LONG_THRESHOLD=600` 已定义但未启用。IndexedDB 缓存只解决「网络慢」，没解决「内存大」。彻底解法：>600s 且 speed=1/pitch=0 时走 `<audio>` + `MediaElementSource` 流式播放（代价：该片段无法 WSOLA 变调） |
| 拖动排序无动画 | 设计如此 | 用插入线而非行位移。行高受 `translateY(-scrollTop)` 影响，位移动画容易错位 |
| 撤销栈是进程内存 | 固有限制 | `_STACKS` 在 `core/history.py` 里是模块级字典，重启服务清空。持久化需要序列化整个状态快照，暂不做 |
| 删除轨道不自动补默认轨 | 设计如此 | 允许删到零视频轨，此时导入片段会报「项目中无可用轨道，请先创建轨道」 |

---

## 十二、验证工具

```
tools/                                共 32 个脚本
├── launch.py                         分离进程启动（自动清端口占用）
├── stat.py                           代码规模统计
├── cleanup_patches.py                清理一次性补丁脚本
├──
├── 【基础设施验证】
├── browser_check.py                  页面加载检查 / 控制台错误
├── browser_interact.py               交互链路（选中/改参/播放/关键帧/撤销）
├── browser_agent_live.py             agent 注册工具实时闭环
├── browser_export.py                 导出 UI 全流程
├── browser_preview_pixel.py          像素级画面验证
├──
├── 【本轮新增的模块验证】
├── verify_track_rename_reorder_type.py  重命名/排序/类型切换（API+浏览器，23 项）
├── verify_track_height.py               轨道高度调整（浏览器，16 项）
├── verify_delete_track_undo.py          删轨撤销完整性（API，9 项）
├── browser_verify_track_ui.py           轨道增删 UI（浏览器，23 项）
├── browser_verify_sub_audio.py          字幕删除 + 音频缓存（浏览器，17 项）
├──
├── 【早期模块验证】
├── verify_assets / audio_live / batch4 / cross_track / export_fit
├── verify_fix5 / fix6 / fixes3 / pitch_crop / render / split_sync
└── diag_*.py                          故障诊断（拖拽/渲染/导出/顶栏/素材/seek）
```

浏览器测试复用系统 Edge（`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`），Playwright 通过 `executable_path` 复用，不下载内核。

### ⚠️ 破坏性测试必须用临时项目

所有会删数据的浏览器测试（删轨道、删片段、改类型）**必须自建临时项目**：

```python
pid = http("POST", API + "/projects", {"name": "__test__"})["data"]["project"]["id"]
try:
    page.goto(BASE + "/?p=" + pid)        # 显式指定，不读 localStorage
    cur = page.evaluate("...state.pid...")
    if cur != pid:
        raise RuntimeError("项目上下文不对，中止以防破坏真实数据")
    ...                                     # 破坏性操作
finally:
    http("DELETE", API + "/projects/" + pid)
```

**教训来源**：早期 `browser_verify_track_ui.py` 读 `localStorage`，直接跑在用户正在编辑的项目上，删掉了真实的 V1 轨道和视频片段。

---

## 变更历史

### 早期

- **初始版本**：P1 后端 + P2 前端 + P3 导出全部落地
- **素材箱**：导入改为登记 + 拖拽上轨，视频源自动拆成视频卡/音频卡
- **WSOLA 音频**：解决「音高改速度」「倍速变调」，预处理 buffer + rate 固定 1.0
- **导出修复**：build 幂等、源等比铺满、擦除 geq 实现
- **拖拽修复**：纵向位移判定、render 守卫、坐标匹配 lane、轨道归属同步
- **UI 完善**：片段改名、多工程切换、素材移除、缩放范围放宽、擦除方向

### 本轮（字幕 / 代理 / 轨道三条线）

**字幕线**
- SUB1 轨从 `effect`（FX1 占位）就地迁移，`normalize()` 幂等保证
- 顶栏加「字幕」按钮，不依赖轨道类型
- 选中字幕块按 `Delete`/`Backspace` 直接删除

**代理 / 大文件线**
- 代理改为 `.part.mp4` + `os.replace` 原子写，半成品永不暴露
- `proxy_state()` 必须「主文件 + `.meta`」同时存在才算就绪
- `.lock` 文件锁防跨进程重复转码（孤儿 ffmpeg 曾把文件写坏）
- 新增 `ensure_audio_proxy()`：`-vn` 只抽音轨，4GB 源 0.3 秒出 36MB
- 转码策略进项目设置 `proxy_mode`（off/auto/always），**默认 off 用源文件**
- `/stream?proxy=1` 代理未就绪回退源文件

**音频缓存线**
- 新增 `static/js/audio_cache.js`：IndexedDB 缓存压缩字节（36MB 而非 355MB PCM）
- 键用 `probe.fingerprint`，源被替换自动失效
- ⚠️ `decodeAudioData` 会 detach 传入 buffer，写缓存必须 `raw.slice(0)`

**轨道线**
- 工具栏「+ 视频轨 / + 音频轨」，轨道头「×」删除
- 轨道名双击重命名、整行拖动排序（插入线指示）、「⇄」切换 video↔audio（仅空轨）
- 轨道下边缘拖动调高度（28~240px，双击恢复 64px）
- 修 `delete_track` 撤销不完整（undo 里补 clip 快照，否则删轨永久不可逆）
- 修浏览器测试破坏真实数据（改自建临时项目）

### 已知待办（按优先级）

1. **长音频流式播放** —— 唯一会在真实大文件上出问题的项
2. **字幕烧录进视频** —— 目前只导出 SRT 侧载
3. 导出关键帧保真（缩放/裁剪/透明度等取片段起点值）
4. 音频缓存 UI（`stats()` 已就绪，缺设置面板入口）
5. 轨道类型杂项（重命名/排序/高度已做，剩「全局默认轨道高度」）

<!-- __EOF__ -->