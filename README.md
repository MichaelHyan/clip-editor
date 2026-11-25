# clip-editor

仿 Premiere 的本地视频剪辑系统。Flask + SocketIO 后端，原生 ESM 前端（零构建），渲染与导出走 ffmpeg。

- **项目根**：`D:/git/clip-editor`
- **服务地址**：`http://127.0.0.1:5173`
- **技术文档**：[`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 快速开始

```bat
cd /d D:\git\clip-editor
python tools\launch.py
```

启动后浏览器打开 `http://127.0.0.1:5173`。

**启动参数**

| 命令 | 作用 |
|---|---|
| `python tools\launch.py` | 启动（端口被占用会自动清理） |
| `python tools\launch.py --restart` | 强制清理端口后启动 |
| `python run.py` | 前台运行（能看实时日志，Ctrl+C 退出） |

日志写 `data/server.log`，pid 写 `data/server.pid`。

**停止**

```bat
:: 杀掉 pidfile 记录的进程
python -c "import os,subprocess; subprocess.run(['taskkill','/F','/PID',open('data/server.pid').read().strip()]); os.remove('data/server.pid')"
```

---

## 重启规则（重要）

按 `ARCHITECTURE.md` 第 9.6 条：

| 改了什么 | 需要做什么 |
|---|---|
| `static/js/*.js`、`static/css/*.css` | **浏览器 Ctrl+F5**，不用重启服务 |
| `templates/*.html` | **必须重启服务**（Flask 模板缓存在 `run.py` 已关，但保险起见） |
| `core/*.py`、`api/*.py`、`run.py` | **必须重启服务** |

---

## 核心设计

1. **真相源在后端** —— 前端 `state.js` 只是镜像，所有变更经 API 落盘后回填
2. **片段独立成文件** —— 一片段一 JSON，天然可跨项目复用
3. **预览与导出共用求值器** —— `core/keyframe.py` 与 `static/js/keyframe.js` 逐行等价
4. **乐观锁** —— `rev` 字段，冲突返回 409 + 最新对象
5. **写操作统一入口** —— `Workspace.lock` 内完成，UI 与 agent 并发安全

---

## 主要功能

| 模块 | 说明 |
|---|---|
| 素材箱 | 导入 = 登记，拖拽上轨；含音轨的视频自动拆成视频卡 + 音频卡 |
| 时间轴 | 轨道增删、重命名、拖动排序、类型切换、高度调整；片段拖动/裁剪/切分/防重叠 |
| 预览 | Canvas 逐帧渲染，裁剪/变换/镜像/效果/擦除/字幕叠加 |
| 检查器 | 视频/音频/效果/字幕 四类参数，支持关键帧打点 |
| 音频 | WSOLA 变调不变速、变速不变调；IndexedDB 缓存音频字节 |
| 字幕 | 独立于 clip 体系，`project.subtitles[]`；可拖动改时间、回格删除；导出 SRT |
| 导出 | ffmpeg filter_complex，预览与导出共用求值器 |
| 效果 | 注册表模式，agent 可动态注册，前端自动长控件 |
| 实时同步 | SocketIO 优先，2s 轮询降级 |

---

## 项目设置

顶栏「设置」按钮 → 对话框：

| 项 | 范围 |
|---|---|
| 画布宽高 | 160~7680 × 120~4320 |
| 帧率 | 1~240 fps |
| 采样率 | 8k / 44.1k / 48k / 96k |
| 背景色 | 任意 |
| **转码策略** | `off`（默认，用源文件）/ `auto`（仅编码不兼容时）/ `always`（总是转码） |

**转码策略默认 `off`** —— 大文件不转码，浏览器直接 Range 流播源文件，最快且不占 CPU。
只有编码浏览器解不了（hevc/mpeg4/wmv3）时才需要切到 `auto`。

---

## 验证与调试

```bat
:: 代码规模
python tools\stat.py

:: 页面加载 + 控制台错误
python tools\browser_check.py

:: 本轮功能的模块验证（全部在临时项目里跑，不碰现有数据）
python tools\verify_track_rename_reorder_type.py   :: 重命名/排序/类型切换 23 项
python tools\verify_track_height.py                :: 轨道高度 16 项
python tools\verify_delete_track_undo.py           :: 删轨撤销 9 项
python tools\browser_verify_track_ui.py            :: 轨道增删 23 项
python tools\browser_verify_sub_audio.py           :: 字幕删除 + 音频缓存 17 项
```

⚠️ **写破坏性测试必须自建临时项目**，不能读 `localStorage` 里的当前项目 —— 曾因此删掉用户真实数据。

前端调试接口：

```js
window.__previewDebug.cache()        // 视频元素 ready/readyState/err
window.__audioDebug.state()          // AudioContext / sources / 缓存键
window.__audioDebug.audioCacheStats() // IndexedDB 音频缓存占用
```

---

## 目录结构

```
clip-editor/
├── run.py                  Flask 入口
├── config.json             端口 / 白名单 / 代理参数
├── api/                    REST 路由（project/clip/asset/media/export/agent…）
├── core/                   业务逻辑（workspace/project/clip/keyframe/export…）
├── static/
│   ├── js/                 前端 ESM 模块
│   ├── css/editor.css      样式
│   └── vendor/             socket.io
├── templates/index.html    单页骨架
├── tools/                  启动 / 验证 / 诊断脚本
├── data/
│   ├── projects/<pid>/     project.json + clips/*.json + assets.json
│   ├── cache/              代理 / 缩略图 / 波形 / probe 缓存
│   ├── exports/<jid>/      导出产物
│   └── effects/registry.json
└── ARCHITECTURE.md         详细技术文档
```

---

## 已知限制（详见 ARCHITECTURE.md 第十一节）

| 项 | 影响 |
|---|---|
| 长音频全量解码 | >600s 音频解码后 PCM 约 355 MB，接近浏览器上限。待改流式播放 |
| 字幕不烧录进视频 | 目前只导出 SRT 侧载文件 |
| 撤销栈是进程内存 | 重启服务清空 |
| 导出关键帧保真 | 缩放/裁剪/透明度等取片段起点值，非逐帧 |

<!-- __EOF__ -->