# clip-editor 技术文档

仿 Premiere 的本地视频剪辑系统。Flask + SocketIO 后端，原生 ESM 前端（零构建），渲染与导出走 ffmpeg。

- **项目根**：`D:/git/clip-editor`
- **服务地址**：`http://127.0.0.1:5173`
- **代码规模**：core+api 约 5600 行，前端 JS 约 5100 行，CSS 约 1400 行

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
│   ├── id / type(video|audio|effect|marker) / index / muted / hidden / locked
│   └── clips[]: 只存 clip_id 引用（顺序无关，渲染按 timeline.start 排）
├── markers[]: 时间轴标记
└── rev / playhead / shared_playhead
```

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
| 预览画布 | 按播放头实时叠字 |

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

**后端校验**：`routes_project.py` 的 `_validate_settings()` 在
`PATCH /projects/<pid>` 前做范围检查，越界返回 400 并带具体数值。

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
- 不兼容时导入**无视体积阈值强制转码代理**（`data/cache/proxy/*.mp4`）
- 代理强制**短 GOP**（`-g fps -keyint_min fps -sc_threshold 0`），否则拖动播放头大面积黑屏

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

---

## 十二、验证工具

```
tools/
├── launch.py               分离进程启动（自动清端口占用）
├── stat.py                 代码规模统计
├── browser_check.py        页面加载检查
├── browser_interact.py     交互链路（选中/改参/播放/关键帧/撤销）
├── browser_agent_live.py   agent 注册工具实时闭环
├── browser_export.py       导出 UI 全流程
├── browser_preview_pixel.py 像素级画面验证
├── verify_*.py             各改动模块的 API 级验证
└── diag_*.py               故障诊断（拖拽/渲染/导出/顶栏）
```

浏览器测试复用系统 Edge（`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`），Playwright 通过 `executable_path` 复用，不下载内核。

---

## 变更历史

- **初始版本**：P1 后端 + P2 前端 + P3 导出全部落地
- **素材箱**：导入改为登记 + 拖拽上轨，视频源自动拆成视频卡/音频卡
- **WSOLA 音频**：解决「音高改速度」「倍速变调」，预处理 buffer + rate 固定 1.0
- **导出修复**：build 幂等、源等比铺满、擦除 geq 实现
- **拖拽修复**：纵向位移判定、render 守卫、坐标匹配 lane、轨道归属同步
- **UI 完善**：片段改名、多工程切换、素材移除、缩放范围放宽、擦除方向

<!-- __EOF__ -->