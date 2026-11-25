---
name: clip-editor
description: "操作本地视频剪辑后端 clip-editor：注册效果工具、读取/修改片段、驱动时间轴编辑与导出。当用户提到剪辑、时间轴、片段、关键帧、效果工具、导出视频，或需要以 agent 身份接管本目录（D:/git/clip-editor）时触发。"
---

# clip-editor 后端操作技能

一个仿 Premiere 的本地视频剪辑系统。后端 Flask + SocketIO，前端原生 ESM 零构建，渲染与导出走 ffmpeg。

**本文件所在目录即项目根**：`D:/git/clip-editor`
**默认地址**：`http://127.0.0.1:5173`
**Python**：`D:\python\py12\python.exe`（3.12.6）
**ffmpeg**：`D:\git\ffmpeg\bin\ffmpeg.exe`（含 libass/rubberband/libvpx/x264/x265/libsoxr）

## 何时使用

✅ 使用：
- 用户要剪辑视频、切分/裁剪片段、调透明度/倍速/音调
- 要注册新效果工具（让前端效果面板自动长出新控件）
- 要读项目时间轴状态、批量改片段参数
- 要导出成片

❌ 不使用：
- 纯 ffmpeg 命令行处理（不经时间轴）→ 直接调 ffmpeg
- 字幕压制/转码等一次性任务 → 不必起服务

## 快速开始

```bash
# 启动（分离进程，关终端仍存活）
cd /d D:/git/clip-editor && python tools/launch.py --restart

# 健康检查
curl -s http://127.0.0.1:5173/api/health

# 项目列表
curl -s http://127.0.0.1:5173/api/projects

# agent 可用的效果工具
curl -s http://127.0.0.1:5173/api/agent/tools
```

停止服务：读 `data/server.pid` 的 pid 后 `taskkill /PID <pid> /F`。

## agent 专属接口（首选）

所有 agent 操作走 `/api/agent/*`。`/api/effects` 是内部别名，功能等价但语义更底层。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/agent/tools` | 工具清单（含 params schema、ui、export 模板） |
| POST | `/api/agent/tools` | 注册/覆盖工具（幂等：同 id 已存在则更新） |
| DELETE | `/api/agent/tools/<id>` | 注销工具（内置不可删） |
| GET | `/api/agent/context?project_id=<pid>` | 项目全景：轨道、片段摘要、时长、playhead、历史 |
| GET | `/api/agent/clip/<cid>?project_id=<pid>` | 单个片段完整 JSON |
| POST | `/api/agent/clip/<cid>/apply-tool` | 把工具挂到片段 `{project_id, tool_id, params?}` |
| PATCH | `/api/agent/clip/<cid>/tool/<iid>` | 改工具实例参数 `{project_id, params?, keyframes?, enabled?}` |
| DELETE | `/api/agent/clip/<cid>/tool/<iid>` | 移除实例 |

**先读 context 再动手**：`GET /api/agent/context` 返回 `clips` 摘要数组（`id/name/kind/track_id/start/duration/rev/tools/markers`），据此定位目标片段，避免盲改。

## 素材箱（导入的正确姿势）

**导入不等于上时间轴**。正确流程是两步：登记素材 → 从素材放到轨道。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/projects/<pid>/assets` | 素材列表，返回 `assets`(源登记) + `cards`(展开卡片) |
| POST | `/api/projects/<pid>/assets` | 登记 `{path, name?}`，幂等（同路径不重复） |
| DELETE | `/api/projects/<pid>/assets/<aid>` | 移出素材箱（**不删源文件**） |
| POST | `/api/projects/<pid>/assets/refresh` | 重新探测（源文件被替换时） |
| POST | `/api/projects/<pid>/assets/<aid>/to-timeline` | 放到轨道 `{role, track_id?, start?, split_av?}` |

**自动拆分**：一个含音轨的视频源登记后展开成两张卡：
- `role=video` → 视频卡（拖到视频轨生成 `media.kind=video` 的 clip）
- `role=audio` → 音频卡（拖到音频轨生成 `media.kind=audio` 的 clip，**路径仍指向视频文件**，导出只取音轨）

`split_av: true` 一次生成视频 + 音频两个 clip，起点对齐（走 `find_free_start` 取两条轨的
共同空位），各自独立裁剪。

**截取导入**：`to-timeline` 支持 `{src_in, src_out}`（秒，可省略）。
素材卡上有「截取」折叠区，可手输入出点或点 ⏱ 取当前播放头时间。
出入点非法（`src_out <= src_in`）返回 400；`src_out` 超出源时长会被钳制到源结尾。

**注意**：`POST /api/projects/<pid>/clips`（旧的直接导入）仍可用，但会绕过素材箱，
音视频绑在一个 clip 里。新代码走素材箱路径。

## 轨道类型与音视频分离约束

**视频片段的音频一律禁用**（`make_clip` 里对 `kind=video` 强制 `audio.enabled=false`）。

原因：音视频绑在一个 clip 里时，音量/音调/裁剪会与画面耦合，无法独立调节。
声音必须走独立的音频片段（`media.kind=audio`，路径可指向视频文件，导出只取音轨）。

检查器「音频」页签对视频片段会显示提示，但**接口层不拦**（`PATCH audio.enabled=true` 仍可用），
这是为了兼容历史数据。新建片段一律禁用。

## 前端状态同步（踩过的大坑）

`state.clips[cid]` 和 `state.project.tracks[].clips` 是**两份数据**：

- `state.clips` 是片段内容
- `state.project.tracks[].clips` 是**轨道归属**（哪些片段在哪条轨）

`renderFrame` 用 `clipsOfTrack(trackId)` 读的是**后者**。跨轨移动只更新前者时，
旧轨道还留着 cid、新轨道里没有 → 视觉上"没落位"、新轨道画面不显示。

**必须成对更新**。`state.js` 里 `applyClip()` 现在会自动调 `syncTrackMembership(cid, oldTrack, newTrack)`，
任何直接改 `state.clips` 的地方都要自己补这一步。

## 拖拽的位移判定

`drag.moved` 不能只判水平位移（`Math.abs(dx) > 2`）——跨轨拖拽常常是**纯垂直**的（dx=0），
会被误判成"没动"，`onDragUp` 直接 return 不换轨。

正确：`Math.abs(dx) > 2 || Math.abs(dy) > 2`。

## 裁剪必须用裁剪后的源尺寸算变换

`applyTransform(c2d, rp, sw, sh, ...)` 的 `sw/sh` 必须是**裁剪后**的源尺寸，
不是原始 `srcW/srcH`。否则 `drawImage(src, sx, sy, sw, sh, -dw/2, -dh/2, dw, dh)` 里
`dw/dh` 是按原始尺寸算的，裁剪后的内容被拉伸回原尺寸——看起来像缩放而不是裁剪。

验证方法：裁掉左右各 25% 后，画面宽高比应从 16:9(1.78) 变成 8:9(0.89)。
比例没变就是被拉伸了。

## 音频变调/变速：必须用 WSOLA（不要用 detune）

**这是本项目音频部分最重要的技术约束。**

Web Audio 的 `detune` 和 `playbackRate` 本质是**同一个重采样倍率**：

```
净速率 = playbackRate × 2^(detune/1200)
```

设了 `detune` 就必然改变速度，无法独立控制。早期尝试用
`playbackRate = speed / pitchRatio` 反向补偿，结果是**音高效果被完全抵消**——用户听不到变调。

**正确做法**：预处理成新 buffer，播放时 `rate` 固定 1.0。

`static/js/audio_process.js` 实现了 WSOLA（Waveform Similarity Overlap-Add）：

| 操作 | 步骤 |
|---|---|
| 变调 N 半音 | ① 重采样到 `1/r` 长度（r = 2^(N/12)，音高变 r 倍、时长变 1/r）<br>② WSOLA 拉伸回原长（时长恢复、音高保持 r 倍） |
| 变速 S 倍 | WSOLA 拉伸到 `1/S` 长度（时长变、音高不变） |

WSOLA 关键参数（`audio_process.js` 顶部）：
- `FRAME = 2048` 帧长，汉宁窗
- `hopIn = FRAME/2`，`hopOut = hopIn × ratio`
- 相似度搜索半径 = `hopIn × 0.25`，在全速率下找最匹配帧避免相位跳变

`audio.js` 的 `getProcessedBuffer(cid)`：
1. 解码完整源 → 按 `[srcIn, srcOut]` 切片
2. `processChannels(channels, speed, semitones)` 应用变调 + 变速
3. `buildBuffer()` 生成新 AudioBuffer，缓存键 `cid|speed|pitch`
4. 播放时 `src.playbackRate.value = 1.0`，offset 直接用墙钟时间

**代价**：WSOLA 是 CPU 密集的，首次应用某个 speed/pitch 组合有几十到几百毫秒延迟；
结果按 `cid|speed|pitch` 缓存，重复播放不再计算。参数变化时才重新处理。

⚠️ 预览用的 WSOLA 是简化实现，音质不如专业算法。**导出端用 ffmpeg 的 `rubberband`
滤镜才是高质量变调**，两者时长语义一致但音质有差距。

## 音频音高与速度（历史记录，已被 WSOLA 取代）

WebAudio 的 `detune` 和 `playbackRate` **都会改变重采样速率**：

```
净速率 = playbackRate × 2^(detune/1200)
```

只设 `playbackRate = speed` + `detune = semitones*100` 时，音高越高播放越快，时长变短。

**必须反向补偿**：

```js
const pitchRatio = Math.pow(2, semitones / 12);
src.playbackRate.value = speed / pitchRatio;
src.detune.value = semitones * 100;
```

这样净速率仍是 `speed`，音高不改变时长。

⚠️ 这是 WebAudio 的固有限制——预览时变调必然伴随轻微音质损失（重采样）。
**导出端用 ffmpeg 的 `rubberband` 滤镜才是真正的变调不变速**。

## 倍速必须重算时间轴时长

`duration_of()` 见到 `timeline.duration` 非空就**直接返回**，
所以 `PATCH video.speed` 后时间轴长度不会变。

`api/routes_clip.py` 的 `patch_clip` 现在检测到这些字段变化时会调
`clip_mod.source_duration()`（忽略缓存、只按源区间和 speed 算）重算 `timeline.duration`：

```
video.speed / audio.speed / video.src_in / video.src_out / audio.src_in / audio.src_out
```

## 时间轴细节（踩过的坑）

**轨道头跟随垂直滚动**：不能用 `heads.scrollTop = scroll.scrollTop`——
`.tl-heads` 是 `overflow:hidden`，内容不溢出时 `scrollTop` 设不进去，永远是 0。

改用 `transform: translateY(-scrollTop)` 作用于内层 `.tl-heads-inner`，
任何情况下都能正确对齐。`syncHeadsScroll()` 在 scroll 事件和 `renderHeads()` 里都调。

**波形必须按剪辑区间裁剪**：`peaks` 覆盖整条源 `[0, srcDur]`，
片段只播放 `[srcIn, srcOut]`，绘制时只取 `peaks[fromIdx, toIdx]` 对应区间，
`fromIdx = srcIn/srcDur × n`。否则裁完片段波形还是整条的样子。

**视频片段不画波形**：只有 `media.kind === "audio"` 的片段才画。
视频轨上画波形没有意义，且会干扰画面缩略图的识别。

## 工程与命名

**片段改名**：检查器标题双击 → 就地输入框 → 回车提交 / Esc 取消 / 失焦提交。
走 `PATCH /clips/<cid>` 的 `{name}`，时间轴标签同步更新。

**多工程**：顶栏 `.proj-switch` 下拉菜单，`projects_ui.js` 管理。
切换 = 改 URL 的 `?p=` 并整页跳转（避免残留状态）。支持新建、重命名（双击项目名）、删除。

## Flask 模板缓存陷阱

`debug=False` 时 Flask 会**缓存 Jinja 模板**，改了 `templates/index.html` 不生效，
现象是"文件明明改了但 DOM 还是旧的"。

`run.py` 必须显式关闭：

```python
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
```

注意：**改 JS/CSS 不需要重启**（静态文件不缓存），**改 HTML 必须重启**。

## 效果控件的即时预览

检查器拖滑块时，若等服务端回包才刷新，拖动过程看不到变化。
`inspector.js` 的 `applyValue()` 现在先**乐观更新**本地 `state.clips` 里的 track 值
并 `bus.emit("clip:changed")`，预览立刻反映，服务端回包后再以真实值覆盖。

## 跨轨拖拽

片段可在**同类型轨道间**拖动（视频↔视频、音频↔音频），跨类型被拒绝。

**关键实现细节**（踩过的坑）：拖拽期间必须给节点加 `pointer-events:none`
（`.dragging-clip` 类），否则 `document.elementFromPoint` 命中的是片段自身，
`.closest('.tl-lane')` 永远返回原轨道，等于没换轨。

**更隐蔽的坑**：`onClipDown` 里如果先调 `selectClip(cid)`，它会触发 `selection:changed`
→ throttle 首次同步执行 `render()` → 所有 `.tl-clip` 节点重建 →
`drag.node` 变成游离节点，`classList.add('dragging-clip')` 加在它身上，`pointer-events` 不生效。

正确顺序：
1. 先设 `drag` 对象、加 `dragging-clip` 类
2. 再调 `selectClip`
3. `render()` 开头加 `if (drag) return`（拖拽期间禁止重建）
4. `onDragMove` 里节点现查（`getDragNode(cid)`），不用缓存引用

跨轨落位走 `POST /clips/<cid>/move`，后端 `find_free_start` 一次到位（不先 patch 再 move）。

## 同轨防重叠

**后端兜底，前端吸附**——两条防线，后端是必须的。

`Workspace.find_free_start(track_id, desired, dur, exclude_id)` 在指定轨道上找第一个
不与已有片段重叠的起点：冲突就向右推到冲突片段结尾，循环直到无冲突。

已接入的路径（任何放片段的地方都必须调）：
- `POST /clips` 导入
- `POST /clips/<cid>/duplicate` 复制
- `POST /clips/<cid>/move` 移动（带 `exclude_id` 排除自己）
- `POST /assets/<aid>/to-timeline` 素材上轨

前端 `timeline.js` 的 `findFreeStart()` 做拖拽时的即时吸附，只是体验层。

**`split_av` 的音视频同步**：分离添加时先在视频轨和音频轨上各找一次空位取交集
（`common = find_free(vtr, start)` → `find_free(atr, common)` → 再回查 `find_free(vtr, common)`），
确保两个 clip 落在同一起点。直接给音频赋 `start` 会绕过防重叠且可能内存/磁盘不一致。

## 裁剪语义（左右边缘互不影响）

`POST /api/projects/<pid>/clips/<cid>/trim` 参数 `{src_in?, src_out?}`：

- **传 `null` 或不传 = 不动这一侧**
- 左边缘裁剪只改 `src_in`，右边缘裁剪只改 `src_out`
- `src_out: null` 表示"到源结尾"，不会被固化成具体数值

⚠️ 两个曾经的 bug（已修）：
1. 前端拖左边缘时把 `src_out` 也传下去，导致 `src_out` 被固化成 `probe.duration`，
   之后右边缘再也拖不长。
2. 右边缘的拖拽上限用了**当前** `src_out` 而非**源媒体时长** `probe.duration`，
   缩一次之后同一个问题会复发。

现在：前端只传变动的那一侧；右边缘上限 = `min(源时长, 右邻居起点)`；
左边缘下限 = `max(源开头, 左邻居终点)`。

## 音频实时参数

播放中改 `audio.gain / pitch / pan / speed` **立即生效**，不会中断播放：

- `audio.js` 的 `activeSources` 保存 `{src, gain, pan, cid}` 节点引用
- `updateActiveParams()` 每 60ms 按当前播放时间重算参数，用 `setTargetAtTime` 平滑过渡
- `clip:changed` 事件**只更新参数，不重排**（早期版本会 `stopAllSources()` 全重建，导致音频断一下）

音频源时间用 `resolveClipAt().audio.srcIn`（支持关键帧与裁剪），不是硬编码 0。

## 工具定义结构

注册即生效——前端 SocketIO 收到 `effect.registry.updated`，效果面板立刻出现新控件，**无需重启、无需改前端代码**。

```json
{
  "id": "film_grain",
  "name": "胶片颗粒",
  "category": "stylize",
  "scope": "video",
  "params": {
    "intensity": {"type":"number","default":0.3,"min":0,"max":1,"step":0.01,"keyframeable":true}
  },
  "preview": {"kind":"canvas","op":"grain"},
  "export":  {"filter":"noise","template":"noise=alls={intensity}:allf=t"},
  "ui": {"panel":"effects","group":"画面风格","order":10,"icon":"film","help":"动态噪点"}
}
```

**字段约束**：
- `id` 只能字母数字下划线短横线；内置 id（`blur/brightness/contrast/saturation/hue/grayscale/sharpen/vignette/audio_gain/audio_echo`）不可覆盖
- `scope`：`video` / `audio` / `both`
- `params.*.type`：`number` / `int` / `bool` / `select` / `color` / `text` / `vec2`；`select` 必须带 `options`
- `keyframeable: true` 的参数，前端会生成关键帧按钮
- `export.template` 用 Python `str.format` 占位，如 `{intensity}`；**没有导出模板的效果会被跳过并在告警中注明**
- `ui.group` 决定前端下拉框分组名，`ui.order` 越小越靠前

**命名说明**：代码里叫 `effect`（`clip.effects` 数组），对外概念叫「工具」。改名会破坏已存数据，故保留。

## 片段数据结构

`data/projects/<pid>/clips/<cid>.json`，一片段一文件：

```jsonc
{
  "id": "clip_xxx", "name": "主镜头", "rev": 3,
  "media": {"path":"D:/video/a.mp4","kind":"video","probe":{}},
  "timeline": {"track_id":"trk_v1","start":0.0,"duration":10.0,"z":0},
  "video": {
    "enabled": true,
    "src_in": {"static":0.0}, "src_out": {"static":null},
    "speed": {"static":1.0}, "scale": {"static":1.0},
    "pos_x": {"static":0.0}, "pos_y": {"static":0.0},
    "rotation": {"static":0.0},
    "anchor_x": {"static":0.5}, "anchor_y": {"static":0.5},
    "crop": {"top":{"static":0},"bottom":{"static":0},"left":{"static":0},"right":{"static":0}},
    "mirror_h": {"static":false}, "mirror_v": {"static":false},
    "erode": {"static":0.0}, "erode_softness": {"static":0.0},
    "opacity": {"static":1.0}, "blend": "normal"
  },
  "audio": {
    "enabled": true, "src_in": {"static":0.0}, "src_out": {"static":null},
    "speed": {"static":1.0}, "pitch": {"static":0.0},
    "gain": {"static":0.0}, "fade_in": {"static":0.0},
    "fade_out": {"static":0.0}, "pan": {"static":0.0}
  },
  "effects": [{"id":"fx_xxx","type":"blur","enabled":true,"params":{"radius":{"static":2}},"meta":{}}],
  "markers": [{"id":"mk_xxx","t":1.5,"label":"点","color":"#ffcc00"}],
  "meta": {"created":0,"updated":0,"author":"ui","tags":[],"notes":""}
}
```

**改片段两条路**：
1. `PATCH /api/projects/<pid>/clips/<cid>` body `{"patch":{"video.opacity":0.5}}` —— 支持点路径，裸值自动包成 `{"static":v}`，功能最全
2. `PATCH /api/agent/clip/<cid>/tool/<iid>` —— 只改工具实例参数

带 `rev` 做乐观锁，冲突返回 409 并附最新对象。

## 关键帧语义

参数值三种形态：

```jsonc
1.0                                          // 裸值，等同 static
{"static": 1.0}                              // 静态
{"keys":[{"t":0,"v":1,"e":"linear"}, {"t":2,"v":0.3,"e":"bezier","h":[0.4,0,0.6,1]}]}
```

- `t` 是**相对片段起点**的秒数，不是时间轴绝对时间
- `e` 缓动：`linear` / `hold` / `ease_in` / `ease_out` / `ease_in_out` / `bezier`
- `bezier` 用 `h: [x1,y1,x2,y2]`，缺省 `[0.4,0,0.6,1]`

**求值器有两份，语义必须一致**：`core/keyframe.py`（后端/导出）与 `static/js/keyframe.js`（前端预览）。改一个必须同步改另一个，否则预览与导出画面偏差。

```bash
# path 支持 video.opacity / video.crop.left / audio.gain / effects.<iid>.<param>
POST /api/projects/<pid>/clips/<cid>/keyframe
{"path":"video.opacity","t":0,"value":1,"e":"ease_in_out"}

DELETE /api/projects/<pid>/clips/<cid>/keyframe
{"path":"video.opacity","t":0}
```

- `GET /api/projects/<pid>/clips/<cid>/resolve?t=2.5` 返回该时刻全部解算值
- `GET .../bake?path=video.opacity&t0=0&t1=5&fps=30` 按帧采样曲线

## 导出

```bash
# 只看命令不执行（排查滤镜链首选）
GET /api/export/preview-cmd/<pid>?dynamic=expr

# 启动导出
POST /api/export/<pid>
{"filename":"output","preset":"medium","crf":20,"dynamic":"expr"}

# 轮询（status: queued|running|done|failed|cancelled）
GET /api/export/jobs/<jid>
# 下载
GET /api/export/jobs/<jid>/download
```

**关键限制**：ffmpeg 只有少数滤镜支持随时间变化。
- `expr` 模式：位置(`pos_x/pos_y`)、色相、响度 等能拟合成时间表达式 → 动态生效
- **缩放、裁剪、透明度、模糊、倍速、音调** 等不支持时间变化 → **取片段起点值**，写入 `warnings`
- 需要最高保真就拆分片段，每段一个静态值
- 快速出片用 `dynamic=static`

产物在 `data/exports/<jid>/`，任务保留最近 40 个。

## 目录速览

```
D:/git/clip-editor/
├── SKILL.md                本文件（agent 接管入口）
├── run.py                  启动入口（Flask + SocketIO）
├── config.json             端口 / 媒体白名单 / 代理 / 默认 1080p60
├── core/
│   ├── keyframe.py         关键帧求值（与前端同语义）
│   ├── clip.py             片段模型 + 点路径 patch + resolve_at
│   ├── project.py          项目/轨道/标记
│   ├── effects.py          效果注册表（内置 + agent 动态）
│   ├── media.py            ffprobe / 代理转码 / 缩略图 / 波形
│   ├── assets.py           素材箱（登记源 / 展开音视频卡）
│   ├── export.py           ffmpeg filter_complex 生成 + 任务队列
│   ├── workspace.py        内存缓存 + 并发锁 + _layout 排序
│   ├── history.py          撤销重做（200 步）
│   └── ops.py              切分 / 复制 / 修剪
├── api/
│   ├── routes_agent.py     ← agent 主入口
│   ├── routes_assets.py    素材箱
│   ├── routes_clip.py      片段 CRUD / 关键帧 / 效果
│   ├── routes_export.py    导出
│   ├── routes_media.py     探测 / 浏览 / 流播
│   └── routes_project.py   项目 / 轨道 / 标记 / 快照
├── static/js/              前端模块
│   ├── audio_process.js    WSOLA 变调/变速（核心）
│   ├── audio.js            播放调度（用处理后的 buffer）
│   ├── projects_ui.js      多工程切换
│   └── ...
├── tools/
│   ├── launch.py           分离进程启动
│   ├── browser_check.py    页面加载检查
│   ├── browser_interact.py 交互验证
│   ├── browser_agent_live.py  agent 实时闭环验证
│   └── browser_export.py   导出 UI 验证
└── data/
    ├── projects/<pid>/     project.json + clips/*.json + snapshots/
    ├── library/            可复用片段库
    ├── effects/registry.json  仅存 agent 注册的效果
    ├── cache/              代理 / 缩略图 / 波形 / probe
    └── test_media/sample.mp4  10s 测试素材
```

## 验证手段

```bash
cd /d D:/git/clip-editor && python tools/browser_check.py      # 页面加载
python tools/browser_interact.py                                # 交互链路
python tools/browser_agent_live.py                              # agent 注册工具闭环
python tools/browser_export.py                                  # 导出 UI
```

Playwright 复用系统 Edge（`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`），不用下载内核。

## 关键约束与坑（务必遵守）

**1. 写文件必须用哨兵 + 只读校验**

写入通道有时会在文件末尾追加非 ASCII 残片。所有文件末尾必须带哨兵：
- Python: `# __EOF__`
- JS: `// __EOF__`
- CSS: `/* __EOF__ */`
- Markdown/HTML: `<!-- __EOF__ -->`

```bash
python -c "import ast;ast.parse(open('path.py',encoding='utf-8').read());print('OK')"
node --check static/js/xxx.js
python -c "s=open('p',encoding='utf-8').read();i=s.find('# __EOF__');print(i>=0 and s[i+9:].strip()=='')"
```

**2. 绝对不要写这种清理脚本**

```python
# 致命：open(p,'w') 先截断，再读就空了，曾清空 19 个文件
open(p,'w').write(open(p).read())
```

正确：先读入内存再写
```python
s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8", newline="\n").write(s)
```

**3. 媒体路径**

默认 `media_trust_all: true`，**信任所有绝对路径**，任意盘符任意目录都能导入。
浏览接口 `/api/media/browse` 不带 `path` 时会自动列出所有可用盘符根 + 用户目录。

如需收紧，把 `config.json` 的 `media_trust_all` 改成 `false`，此时只允许 `media_roots` 内的路径。

⚠️ 注意 `media_roots` 里的盘符根必须写成 `"D:/"`（带斜杠）。写成 `"D:"` 会被 Windows 解析成"当前工作目录"，导致判定失效。

**4. 前端无构建**

原生 ES Module，浏览器直接 import。改 JS 不用重启，刷新页面即可；改 Python 必须重启。

**5. 并发安全**

UI 与 agent 同时改同一项目时，写操作在 `Workspace.lock` 内完成，配合 `rev` 乐观锁。批量改多片段要逐个 `PATCH`，不要并发猛打。

**6. 轨道顺序**

`track.index` 小的在上层（V2 盖 V1）。同轨内片段按 `timeline.start` 自动排序（`workspace._layout`）。

**7. 浏览器编码兼容（预览黑屏的头号原因）**

浏览器只认 `h264 / vp8 / vp9 / av1`（音频 `aac / mp3 / opus / vorbis / flac`）。
源文件是 `mpeg4`(MPEG-4 Part 2) / `hevc` / `wmv3` 等时，`<video>` 直接报错，预览全黑。

系统已自动处理：
- `media.probe()` 返回 `browser_ok` / `browser_issues`
- 不兼容时导入会**无视体积阈值**强制转码代理（`data/cache/proxy/*.mp4`）
- 前端 `preview.js` 解码失败会自动请求代理并重载
- `GET /api/media/playable?path=` 可主动查询是否需要代理

**代理转码强制短 GOP**（`-g fps -keyint_min fps -sc_threshold 0`，即每秒 1 个关键帧）。
原因：x264 默认 250 帧一个关键帧（≈10s），浏览器 seek 到关键帧之间时解码器给不出帧，
拖动播放头会大面积黑屏。旧代理没有 `.meta` 标记，会被自动判定为长 GOP 并重建。

**排查黑屏的顺序**：
1. `GET /api/media/playable?path=` → `browser_ok: false` 就是编码问题
2. `curl -X POST /api/media/proxy?path=` 手动转码，看能否成功
3. 用 `ffmpeg -ss <t> -i <源> -frames:v 1 out.jpg` 抽帧——**确认是不是素材本身就是黑场**
   （黑底白字片头、星空、淡入都会让像素检测误判成"没画面"）
4. 页面里 `window.__previewDebug.cache()` 看 `ready / readyState / err`
5. `window.__previewDebug.probeFrame()` 看 video 元素本身有没有帧（`videoPixels > 0`）
6. `window.__previewDebug.dbg.bail` 看渲染在哪一步提前返回

**8. 预览时间必须用解算值**

`renderClip` 里的源时间 = `srcIn + (absT - clipStart) * speed`。
`srcIn` 必须从 `resolveClipAt()` 的 `video.srcIn` 取（支持关键帧），**不能硬编码 0**，
否则用户裁剪入点后预览画面会错位。

## 常见任务配方

**给片段加可调关键帧的效果**
```bash
# 1. 找片段
curl -s "http://127.0.0.1:5173/api/agent/context?project_id=<pid>"
# 2. 挂效果
curl -X POST http://127.0.0.1:5173/api/agent/clip/<cid>/apply-tool \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","tool_id":"blur","params":{"radius":5}}'
# 3. 读回拿实例 id
curl -s "http://127.0.0.1:5173/api/agent/clip/<cid>?project_id=<pid>"
# 4. 给实例参数打关键帧
curl -X PATCH http://127.0.0.1:5173/api/agent/clip/<cid>/tool/<iid> \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","keyframes":[{"path":"radius","t":0,"value":0},{"path":"radius","t":3,"value":20,"e":"ease_in_out"}]}'
```

**注册全新效果工具并立即使用**
```bash
curl -X POST http://127.0.0.1:5173/api/agent/tools \
  -H "Content-Type: application/json" -d '{
    "id":"zoom_pulse","name":"变焦脉冲","category":"stylize","scope":"video",
    "params":{"amount":{"type":"number","default":0.2,"min":0,"max":2,"step":0.01,"keyframeable":true}},
    "preview":{"kind":"css","template":"scale(1+{amount})"},
    "export":{"filter":"zoompan","template":"zoompan=z=1+{amount}"},
    "ui":{"group":"自定义","order":1}
  }'
```

**批量调响度**
```bash
curl -X PATCH http://127.0.0.1:5173/api/projects/<pid>/clips/<cid> \
  -H "Content-Type: application/json" -d '{"patch":{"audio.gain":-3}}'
```

## 已知待改进

- 工具注销后已挂载实例变「孤儿」：Inspector 无 schema，只显示原始值不显示滑块（数据不丢）。理想做法是注销改软禁用。
- 关键帧只能打点，没有曲线拖拽编辑。
- 导出时同轨片段重叠不报错，靠 overlay `enable` 时间窗共存，后画的盖前面的。
- 无同轨重叠检测、磁吸开关 UI、右键菜单。

## 变更历史

- **音频改用 WSOLA**：彻底解决「音高改速度」和「倍速变调」。`detune` 与 `playbackRate` 是同一重采样倍率，无法独立控制；改为预处理 buffer（重采样 + WSOLA 时间拉伸），播放时 rate 固定 1.0。新增 `audio_process.js`。
- **轨道头跟随滚动**：改用 `transform: translateY`，不能用 `scrollTop`（`.tl-heads` 是 `overflow:hidden`，不溢出的 scrollTop 恒为 0）。
- **波形按剪辑区间裁剪**：`drawWave` 增加 `srcIn/srcOut/srcDur` 参数，只绘对应索引区间。视频片段不再画波形。
- **片段改名**：检查器标题双击就地编辑。
- **多工程**：顶栏项目切换器，支持切换/新建/重命名/删除。新增 `projects_ui.js`。
- **Flask 模板缓存**：`debug=False` 会缓存模板，`run.py` 显式开启 `TEMPLATES_AUTO_RELOAD`。

- **轨道归属同步**：`applyClip` 自动维护 `project.tracks[].clips`。跨轨后 V1 画面不显示的根因就是这里——只更新了 `state.clips`，渲染读的是轨道数组。
- **跨轨拖拽修复（第三轮）**：`drag.moved` 只判水平位移导致纯垂直拖动被当成"没动"；改用 `laneAtY()` 按 Y 坐标匹配轨道，摆脱 `elementFromPoint` 的不确定性。
- **裁剪保持比例**：`applyTransform` 用裁剪后的 `sw/sh` 算 `dw/dh`。之前用原始尺寸，裁剪内容被拉伸。
- **音高不加速**：`playbackRate` 反向补偿 `pitchRatio`，净速率保持 `speed`。
- **倍速改时长**：`patch_clip` 检测 speed/src_in/src_out 变化时用 `source_duration()` 重算。
- **效果即时预览**：检查器滑块乐观更新本地状态，不等服务端回包。

- **跨轨拖拽修复**：根因是 `selectClip` 触发的同步 `render()` 重建节点导致 `drag.node` 游离，`pointer-events:none` 失效。修法：先设 drag 再 selectClip、render 加拖拽守卫、节点现查。视频可在视频轨间拖、音频在音频轨间拖，跨类型被拒。
- **视频片段禁用音频**：`make_clip` 对 `kind=video` 强制 `audio.enabled=false`。声音必须走独立音频片段，音量/音调/裁剪与画面解耦。检查器音频页显示提示，接口层保留 `PATCH` 能力兼容旧数据。

- **同轨防重叠**：后端 `Workspace.find_free_start()` 兜底，接入导入/复制/移动/素材上轨四条路径；前端拖拽吸附作为体验层。修复 `split_av` 音视频起点被硬覆盖导致不同步的问题。
- **修复右边缘拉不长**：拖拽上限改用源媒体时长 `probe.duration`（之前用当前 `src_out`，缩一次就锁死）。
- **素材截取导入**：`to-timeline` 支持 `src_in/src_out`，素材卡加截取折叠区（手输入出点 / ⏱ 取播放头 / 重置）。

- **素材箱 + 音视频分离**：导入改为登记素材，视频源自动展开成视频卡 + 音频卡，拖到轨道才生成 clip。新增 `core/assets.py` + `api/routes_assets.py`，左侧 240px 素材栏可折叠。
- **音频参数实时生效**：`activeSources` 保留节点引用，播放中改 gain/pitch/pan/speed 直接作用到活动节点（`setTargetAtTime` 平滑），不再全量重排导致播放中断。
- **修复裁剪语义**：左边缘只改 `src_in`，右边缘只改 `src_out`，互不固化。之前拖左边缘会把 `src_out` 固化成固定值，右边缘再也拖不长。

- **修复预览黑屏**（三层叠加）：①浏览器不支持 mpeg4 → 自动转码代理；②长 GOP seek 出黑帧 → 代理强制每秒关键帧；③硬编码 `srcIn=0` → 改用解算值。另修 `launch.py` 双实例端口抢占（Windows 允许多进程绑同一端口，请求被随机分配）。

- **媒体白名单改为信任模式**：`media_trust_all: true`。修复三个失效点——`media_roots` 的 `"D:/"` 被 rstrip 成 `"D:"`、`is_media_allowed` 拒绝项目外文件、`browse` 目录 403。现在任意盘符可导入。

- **初始版本**：P1 后端骨架（clip/project/keyframe/effects/media/store/history）、P2 编辑器前端（时间轴/Canvas 预览/WebAudio/Inspector/agent 工具接口）、P3 导出管线（filter_complex 生成 + 任务队列 + UI）全部完成并实测通过。
- 关键教训：写入通道残片问题、`open(p,'w')` 截断事故、`effects.<iid>.<param>` 数组路径解析 bug、导出 overlay 表达式含逗号需引号包裹、每 clip 只分配一次输入。

<!-- __EOF__ -->