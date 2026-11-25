# 进度记录

> 最后更新：2026-11-25
> 状态：**服务已停止**，所有代码/文档/验证脚本已落盘
> 详细技术文档：[`ARCHITECTURE.md`](ARCHITECTURE.md)（1076 行）

---

## 一、当前状态快照

| 项 | 值 |
|---|---|
| 服务 | **已停止**（端口 5173 已释放，pidfile 已删） |
| 代码规模 | 74 文件 / 18658 行 |
| core | 14 文件 / 3840 行 |
| api | 10 文件 / 2188 行 |
| static/js | 17 文件 / 6343 行 |
| static/css | 1 文件 / 1607 行 |
| tools | 32 脚本 / 4680 行 |
| 项目数据 | `data/projects/proj_00c13582b005`（唯一项目） |
| 缓存 | `data/cache/{proxy,thumb,wave,probe}` |
| 残留补丁脚本 | **无**（已清理 23 个 + 事故备份 1 个） |

### 恢复工作

```bat
cd /d D:\git\clip-editor
python tools\launch.py
```

浏览器打开 `http://127.0.0.1:5173`。

---

## 二、本轮完成的功能

### 字幕线

| 功能 | 入口 |
|---|---|
| SUB1 轨从 `effect`（FX1 占位）就地迁移 | `normalize()` 的 `_ensure_subtitle_track()`，幂等 |
| 顶栏「字幕」按钮 | 不依赖轨道类型，永远可见 |
| SUB 轨头「+」 | 在播放头处加字幕 |
| 字幕块拖动改时间 / 拖两边改时长 | 时间轴 SUB 轨 |
| **选中字幕后回格删除** | `Delete` / `Backspace` |
| 检查器字幕编辑 | 内容/时间/字号/颜色/描边/位置/对齐/删除 |

### 代理 / 大文件线

| 问题 | 修复 |
|---|---|
| 半成品代理被当成可用 | 写 `.part.mp4` → 成功才 `os.replace` 原子改名 |
| 完成标记从不参与判定 | `proxy_state()` 必须「主文件 + `.meta`」同时存在 |
| 两个 ffmpeg 抢写同一文件 | `.lock` 文件锁（`O_EXCL`，2h 过期） |
| 音频被迫下载整个 4GB 源 | `ensure_audio_proxy()`：`-vn` 抽音轨，4GB → 36MB / 0.3s |
| 无谓的大文件转码 | 转码策略进设置 `proxy_mode`，**默认 off 用源文件** |
| 代理未就绪时喂坏文件 | `/stream?proxy=1` 回退源文件 |

### 音频缓存线

| 项 | 说明 |
|---|---|
| 新增 `static/js/audio_cache.js` | IndexedDB 缓存**压缩字节**（36MB）而非 PCM（355MB） |
| 缓存键 | `probe.fingerprint:kind` 优先，`path:size:kind` 兜底 |
| 上限 | 单条 256MB，总量 1GB，超了按 `saved` 从旧删 |
| 关键坑 | `decodeAudioData` 会 **detach** 传入 buffer，写缓存必须 `raw.slice(0)` |

### 轨道线

| 功能 | 操作 |
|---|---|
| 加视频轨 | 工具栏「+ 视频轨」，置于最上层（`index: -1`） |
| 加音频轨 | 工具栏「+ 音频轨」，置于最下层（`index: 9999`） |
| 删除轨道 | 轨道头「×」（带片段二次确认，字幕轨不给） |
| 重命名 | 轨道名**双击**，Enter 提交 / Esc 取消 |
| 拖动排序 | 轨道行**按住拖动**，插入线指示落点 |
| 类型切换 | 轨道头「⇄」，仅**空轨道**且仅 `video ↔ audio` |
| 高度调整 | 轨道头**下边缘拖动**（28~240px），双击恢复 64px |

### 顺带修的两个真问题

| 问题 | 根因 | 修复 |
|---|---|---|
| 删轨后撤销无法恢复片段 | `drop_clip()` 之后再记 undo，只记了 project 状态 | drop 前先把每个 clip 快照塞进 undo 列表 |
| 浏览器测试破坏真实数据 | 读 `localStorage` 的当前项目 | 自建临时项目 + 二次校验 `state.pid` + finally 删除 |

---

## 三、验证脚本清单

全部在临时项目里跑，结束自动清理，不碰现有数据。

| 脚本 | 项数 | 覆盖 |
|---|---|---|
| `verify_track_rename_reorder_type.py` | **23/23** | 重命名（空名/超长/Esc）、类型切换（空轨可改/非空拦截/非法类型 400）、排序（生效/index 连续/不完整 order 不丢轨）、真实鼠标拖动 |
| `browser_verify_track_ui.py` | **23/23** | 加视频轨置顶 V3 / 加音频轨置底 A2 / index 连续 / 删除按钮数 / 删空轨 / 删带片段轨 + 撤销 / 字幕轨保护 |
| `browser_verify_sub_audio.py` | **17/17** | 字幕新增→选中→Delete/Backspace 删除；IndexedDB `put/get/remove/stats` 与缓存键格式 |
| `verify_track_height.py` | **16/16** | 把手存在 / 向下向上拖动 / lane 同步 / 落盘刷新持久 / 28-240 钳制 / 双击恢复 / 不误触发排序 |
| `verify_delete_track_undo.py` | **9/9** | 删轨 → 片段消失 → 撤销 → 轨道回来 + 片段内容回来 + 重新挂回轨道 |

**合计 88 项断言，全部通过。**

### 早期脚本（仍在）

`browser_check` / `browser_interact` / `browser_agent_live` / `browser_export` /
`browser_preview_pixel` / `verify_assets` / `verify_audio_live` / `verify_batch4` /
`verify_cross_track` / `verify_export_fit` / `verify_fix5` / `verify_fix6` /
`verify_fixes3` / `verify_pitch_crop` / `verify_render` / `verify_split_sync` /
`diag_*`（6 个）

---

## 四、文档清单

| 文件 | 行数 | 内容 |
|---|---|---|
| `ARCHITECTURE.md` | **1076** | 完整技术文档，含 17 条修改记录、已知约束与坑、验证工具索引 |
| `README.md` | 156 | 快速开始 / 重启规则 / 功能概览 / 目录结构 |
| `FIX_AUDIO_VIDEO_PLAYBACK.md` | 388 | 大视频黑屏无声的六重根因与修复 |
| `FIX_SUBTITLE_DELETE_AUDIO_CACHE.md` | 326 | 字幕回格删除 + 音频 IndexedDB 缓存 |
| `FIX_TRACK_ADD_DELETE.md` | 286 | 轨道增删 + 删轨撤销修复 + 测试隔离 |
| `FIX_TRACK_HEIGHT.md` | 285 | 轨道高度调整 + 双击失效的坑 |
| `SUBTITLE_ENTRY_FIX.md` | 262 | 字幕入口不可见（FX1 → SUB1 迁移） |
| `SKILL.md` | 642 | （早前文档，未改动） |

---

## 五、已知问题（按优先级）

### P1：真实大文件上会出问题

**长音频流式播放** —— 唯一一项。

- 现状：>600s 音频全量 `decodeAudioData` 进内存，PCM 约 **355 MB**（切片翻倍约 700 MB），接近浏览器上限
- `audio.js` 第 17 行 `LONG_THRESHOLD = 600` **已定义但从未引用**
- IndexedDB 缓存只解决「网络慢」，没解决「内存大」
- 彻底解法：>600s 且 `speed=1/pitch=0` 时走 `<audio>` + `MediaElementSource` 流式播放
- 代价：该片段无法 WSOLA 变调（但没改音高/速度时本来也不需要）

### P2：功能缺失

| 项 | 说明 |
|---|---|
| 字幕不烧录进视频 | 目前只导出 SRT 侧载。烧录需 `subtitles`/`ass` 滤镜 + 字体路径处理 |
| 导出关键帧保真 | 缩放/裁剪/透明度/模糊/倍速/音调/擦除取片段起点值，非逐帧。位置/色相/响度已支持时间表达式 |
| 音频缓存无 UI | `stats()` 已就绪，设置对话框缺「音频缓存 X MB / 清空」入口，目前用 `window.__audioDebug` |

### P3：体验优化

| 项 | 说明 |
|---|---|
| 关键帧只能打点 | 没有曲线拖拽编辑 |
| 字幕无法跨项目复用 | 存在于项目级 `subtitles[]`，没有像 clip 那样的片段库 |
| 工具注销后已挂载实例变孤儿 | Inspector 无 schema，只显示原始值不显示滑块（数据不丢） |
| 全局默认轨道高度 | 每条轨独立 `height`，新轨固定 64 |

### 固有限制（不打算改）

| 项 | 说明 |
|---|---|
| WSOLA 音质 | 预览用简化实现，导出端用 rubberband 更佳 |
| 撤销栈是进程内存 | `_STACKS` 重启即清。持久化需序列化整个状态快照 |
| 拖动排序无动画 | 用插入线而非行位移。行高受 `translateY(-scrollTop)` 影响，位移动画容易错位 |
| 删除轨道不自动补默认轨 | 允许删到零视频轨，此时导入报「项目中无可用轨道，请先创建轨道」 |
| 字幕轨被手工删除 | `normalize()` 下次读盘自动补一条（字幕数据不丢） |

---

## 六、下一步建议

**优先做长音频流式播放**——它是唯一一个会在真实大文件上出问题的遗留项，其他都是可选优化。

实现要点（提前记下，避免重复踩坑）：

1. `audio.js` 的 `scheduleAll` 里按 `clipDuration` 分流：>600s 且 `speed=1 && pitch=0` 走 `<audio>`
2. `<audio>` 用 `mediaUrl(path, proxy, audio=true)` 指向音频代理（36MB 而非 4GB）
3. `createMediaElementSource(node)` 接入 `masterGain`，与 BufferSource 共用音量/声像链路
4. `audio.enabled` / `gain` / `pan` 仍要实时生效（`MediaElementSource` 后接 `GainNode`）
5. 一旦检测到 `speed != 1` 或 `pitch != 0`，切回 BufferSource 路径并提示「变调需要预处理，长音频请耐心等待」
6. 缓存边界：`<audio>` 走浏览器自带流式缓存，不要再写 IndexedDB

验证时用 `D:/a/out/mh/6.4.mp4`（925s，音频代理 36.9MB），确认：
- 内存占用（DevTools Memory）不再飙升到 700MB
- 播放头任意位置 seek 后有声
- 音量/静音仍生效

---

## 七、本轮踩到的坑（供以后参考）

1. **稳定排序的 index 语义**
   `append` + `sort` + 重编号，传 `index=0` 不会覆盖原有首位的轨，要抢首位必须传负数。

2. **撤销快照必须在破坏性动作之前**
   `drop_clip()` 之后再记 undo，等于什么都没记。

3. **`pointerup` 里无条件 `render()` 会破坏双击**
   双击 = down/up + down/up + dblclick，第一次 up 就重建节点，后续 dblclick 落在游离节点上。判「有没有实际变更」，没变更就什么都不做。

4. **`decodeAudioData` 会 detach 传入的 ArrayBuffer**
   写缓存必须 `raw.slice(0)` 先复制；且复制前判大小，否则 4GB 源 fallback 时复制一次就是灾难。

5. **「文件存在」不等于「文件可用」**
   异步产出物必须有显式完成标记，且**所有消费方都以标记为准**。写标记但不读标记，等于没写。

6. **进程内锁管不住跨进程**
   `_PROXY_JOBS` 是字典，重启即失效。跨进程必须用文件系统锁（`O_EXCL`）。

7. **破坏性自动化测试必须自带隔离环境**
   临时项目、临时目录、临时端口都行，就是不能用「用户当前正在用的」。

8. **结构性入口不能绑死在一个可变状态上**
   字幕入口曾绑在「轨道类型」上，老项目那条轨是 `effect`，导致完全看不到入口。

<!-- __EOF__ -->