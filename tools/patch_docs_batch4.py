# -*- coding: utf-8 -*-
"""更新 ARCHITECTURE.md：字幕系统、项目设置、全角度擦除、素材卡布局修复。"""
import io

BASE = "D:/git/clip-editor"


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


p = BASE + "/ARCHITECTURE.md"
s = read(p)

if "字幕系统" in s:
    print("already patched")
    raise SystemExit(0)

# ---------------------------------------------------------------- 1. 数据模型补字幕
old = '''`data/projects/<pid>/` 结构：'''
new = '''### 字幕（独立于 clip 体系）

字幕存在 `project.subtitles[]`，**不占用 clip**，由 `SUB` 轨统一管理：

```jsonc
{
  "id": "sub_xxx",
  "start": 1.0,          // 时间轴绝对时间（秒）
  "end": 4.0,
  "text": "字幕内容\\n支持换行",
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

`data/projects/<pid>/` 结构：'''
if old not in s:
    raise SystemExit("数据模型锚点缺失")
s = s.replace(old, new, 1)
print("  [ok] 数据模型补字幕")

# ---------------------------------------------------------------- 2. 渲染管线补字幕
old2 = '''**视频元素代理兜底**：浏览器解不了的编码（mpeg4/hevc/wmv3）自动转码 H.264 代理并重载。见「编码兼容」。'''
new2 = '''**视频元素代理兜底**：浏览器解不了的编码（mpeg4/hevc/wmv3）自动转码 H.264 代理并重载。见「编码兼容」。

**字幕叠在最上层**：`renderFrame` 末尾调 `drawSubtitle(ctx, sub, W, H)`，
不受片段变换影响。字号按画布宽度归一化（`fontSize / 1920 × canvasWidth`），
换分辨率时字幕视觉比例不变。'''
if old2 not in s:
    raise SystemExit("渲染管线锚点缺失")
s = s.replace(old2, new2, 1)
print("  [ok] 渲染管线补字幕")

# ---------------------------------------------------------------- 3. 新章节：字幕系统 + 项目设置
old3 = '''## 七、效果/工具系统'''
new3 = '''## 六点五、字幕系统

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

## 七、效果/工具系统'''
if old3 not in s:
    raise SystemExit("章节锚点缺失")
s = s.replace(old3, new3, 1)
print("  [ok] 新增字幕/设置章节")

# ---------------------------------------------------------------- 4. 擦除章节改全角度
old4 = '''**线性擦除（erode）**：用 `destination-out` + 线性渐变遮罩，比逐像素快几个数量级。'''
new4 = '''**线性擦除（erode，全角度）**：用 `destination-out` + 线性渐变遮罩，比逐像素快几个数量级。

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
```'''
if old4 not in s:
    raise SystemExit("擦除章节锚点缺失")
s = s.replace(old4, new4, 1)
print("  [ok] 擦除改全角度")

# ---------------------------------------------------------------- 5. 坑章节补一条
old5 = '''### 10. 写文件要带哨兵'''
new5 = '''### 10. flex 容器里的卡片会被压缩

`.assets-body` 是 `display:flex; flex-direction:column`，`.asset-card` 作为
flex item 默认 `flex-shrink:1`。素材一多，总高超过容器就会被**均匀压缩**，
最先消失的是信息区（名称/截取/按钮）——现象是"只看到缩略图，没有按钮"。

必须显式 `flex-shrink: 0`，让容器滚动而不是压缩内容。

### 11. 写文件要带哨兵'''
if old5 not in s:
    raise SystemExit("坑章节锚点缺失")
s = s.replace(old5, new5, 1)
print("  [ok] 坑章节补 flex 压缩")

# ---------------------------------------------------------------- 6. 变更记录
old6 = '''## 十一、当前已知问题'''
new6 = '''### 5. 素材箱不可见

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

## 十一、当前已知问题'''
if old6 not in s:
    raise SystemExit("变更记录锚点缺失")
s = s.replace(old6, new6, 1)
print("  [ok] 变更记录补 4 条")

# ---------------------------------------------------------------- 7. 已知问题表更新
old7 = '''| 旧片段音视频绑定 | 数据遗留 | 之前按旧方式导入的片段音视频在同 clip 内，不受新约束影响。需重新导入拆分 |'''
new7 = '''| 旧片段音视频绑定 | 数据遗留 | 之前按旧方式导入的片段音视频在同 clip 内，不受新约束影响。需重新导入拆分 |
| 字幕不烧录进视频 | **待实现** | 目前只导出 SRT 侧载。烧录需 `subtitles`/`ass` 滤镜 + 字体路径，下一轮补 |
| 字幕无法跨项目复用 | 未实现 | 字幕存在项目级 `subtitles[]`，没有像 clip 那样的片段库 |'''
if old7 in s:
    s = s.replace(old7, new7, 1)
    print("  [ok] 已知问题表更新")

# ---------------------------------------------------------------- 8. 目录速览补文件
s = s.replace(
    "│   ├── projects_ui.js      多工程切换",
    "│   ├── projects_ui.js      多工程切换\n"
    "│   ├── subtitles.js        字幕系统（渲染/CRUD/预览绘制）\n"
    "│   ├── settings_ui.js      项目设置对话框", 1)

write(p, s)
print("patched ARCHITECTURE.md, len", len(s))

# __EOF__