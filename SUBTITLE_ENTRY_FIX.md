# 字幕创建入口不可见 —— 根因与修复

> 修复日期：2026-11-25
> 影响范围：`core/project.py`、`static/js/state.js`、`static/js/main.js`、
> `static/js/inspector.js`、`templates/index.html`、老项目数据迁移

---

## 一、问题现象

打开主界面（项目 `proj_00c13582b005`），**完全看不到任何创建字幕的入口**：

- 时间轴 SUB 轨头没有「+」按钮
- 时间轴上没有字幕轨
- 检查器空状态提示里写的「点 SUB 轨旁的『+ 字幕』」指向一个不存在的东西
- `project.subtitles` 永远是 `[]`，导出 SRT 也无内容

---

## 二、根因

字幕的**全部入口**都挂在「轨道类型 == `subtitle`」这一条件上。

`static/js/timeline.js` 的 `renderHeads()`：

```js
tr.type === "subtitle"
  ? [headBtn("+", "在播放头处加字幕", false, addSubtitleHere)]
  : [ 静音 / 隐藏 / 锁定 ]
```

`renderLanes()` 同理：

```js
if (tr.type === "subtitle") {
  for (const sub of subtitles.listSubtitles()) lane.appendChild(renderSubtitleEl(sub));
}
```

而 `core/project.py` 里：

- `make_project()` **新建**项目会建 `SUB1`（subtitle 轨）✅
- `normalize()` **不补** subtitle 轨 ❌

于是：

| 项目 | 第 3 条轨 | 字幕入口 |
|---|---|---|
| 新建项目 | `subtitle`（SUB1） | 正常 |
| 老项目 `proj_00c13582b005` | `effect`（FX1，早期占位，空轨） | **完全没有** |

`FX1` 是字幕系统落地之前就存在的 effect 轨占位，架构文档里也写了
「FX1（effect 轨）实际没用上，改为 SUB1 字幕轨」——但**只改了新建逻辑，
没有做存量数据迁移**。这就是「文档说有、界面看不到」的原因。

> 附带问题：`state.selectClip()` 不清 `selectedSubtitleId`，导致选中片段后
> 检查器仍停留在字幕编辑页，改片段参数看不到控件。

---

## 三、修复方案

### 1. 后端：`normalize()` 补幂等迁移

`core/project.py` 新增 `_ensure_subtitle_track()`，在 `normalize()` 排序前调用：

```python
def _ensure_subtitle_track(p):
    tracks = p.get("tracks") or []
    if not tracks:
        return p
    if any(t.get("type") == "subtitle" for t in tracks):
        return p                      # 已有字幕轨，什么都不做

    # 1) 优先复用一条空的 effect 轨（早期占位），就地改型
    for tr in tracks:
        if tr.get("type") == "effect" and not (tr.get("clips") or []):
            tr["type"] = "subtitle"
            if str(tr.get("name") or "").upper().startswith("FX"):
                tr["name"] = "SUB1"
            return p

    # 2) 没有可复用的空轨：插到最后一条 video 轨之后
    tr = make_track("subtitle", "SUB1", 0)
    idx = 0
    for i, t in enumerate(tracks):
        if t.get("type") == "video":
            idx = i + 1
    tracks.insert(idx, tr)
    for i, t in enumerate(tracks):
        t["index"] = i
    return p
```

**设计要点**：

- **就地改型而非删旧建新** —— 保留原 `track_id`（`trk_ef41a77`），
  避免历史操作记录 / 快照 / 外部引用里的 track_id 失效
- **只复用空轨** —— 有片段的 effect 轨不动，否则片段会挂到字幕轨上
- **幂等** —— `normalize()` 每次读盘都会跑，已有 subtitle 轨立即返回
- 新建项目路径不受影响（`make_project` 本来就建 SUB1）

### 2. 前端：顶栏「字幕」按钮（不依赖轨道类型）

只修数据还不够——用户第一眼仍然是找按钮。顶栏加一个**永远可见**的入口：

`templates/index.html`

```html
<button class="btn" id="btn-subtitle"
        title="在播放头处添加字幕（不依赖轨道类型）">字幕</button>
```

`static/js/main.js`

```js
import * as subtitles from "./subtitles.js";

// bindHeader() 内
const subBtn = $("btn-subtitle");
if (subBtn) subBtn.addEventListener("click", addSubtitleFromTopbar);

// 顶栏「字幕」：在播放头处加一条字幕并直接进入编辑
async function addSubtitleFromTopbar() {
  if (!S.state.pid) return;
  S.setPlaying(false);
  const sub = await subtitles.addSubtitleAtPlayhead("新字幕");
  if (!sub) return;
  subtitles.selectSubtitle(sub.id);
  toast("已添加字幕，在右侧检查器编辑内容", "ok");
}
```

这样即使某天轨道数据再出问题，字幕创建也不会失联。

### 3. 前端：选中片段时退出字幕编辑页

`static/js/state.js` 的 `selectClip()`：

```js
export function selectClip(cid, opts = {}) {
  if (state.selectedClipId === cid && !opts.force) return;
  state.selectedClipId = cid || null;
  state.selectedToolInstance = null;
  // 选中片段即离开字幕编辑页，否则检查器会一直停在字幕面板
  if (cid) state.selectedSubtitleId = null;
  bus.emit("selection:changed", state.selectedClipId);
  ...
}
```

检查器 `build()` 里字幕优先级高于片段（先查 `selectedSubtitleId`），
不清就会「点了片段但右侧还是字幕表单」。

### 4. 检查器空状态文案对齐

`static/js/inspector.js`：

```js
"未选中片段\n在时间轴上点选一个片段以编辑属性\n\n"
+ "要加字幕：点顶栏「字幕」按钮，或 SUB 轨头的「+」"
```

原来写的是「+ 字幕」，但按钮实际文字是「+」，文案对不上。

### 5. 老项目数据迁移

`tools/patch_subtitle_visibility.py` 的 `migrate_data()` 遍历
`data/projects/*`，逐个 `load_project()`（内部走 `normalize()`
触发迁移）后 `save_project()` 落盘。

执行结果：

```
迁移 proj_00c13582b005 -> ['video', 'video', 'subtitle', 'audio']
已修改： ['core/project.py', 'static/js/state.js', 'templates/index.html',
          'static/js/main.js', 'static/js/inspector.js']
```

`project.json` 迁移后：

```jsonc
{
  "id": "trk_ef41a77",        // ← track_id 保留
  "type": "subtitle",         // ← 原来是 effect
  "name": "SUB1",             // ← 原来是 FX1
  "index": 2,
  "clips": []
}
```

---

## 四、验证

| 项 | 方法 | 结果 |
|---|---|---|
| Python 语法 | `ast.parse(core/project.py)` | OK |
| JS 语法（ESM） | `node --input-type=module --check < xxx.js` × 3 | main/state/inspector 全部 OK |
| 迁移生效 | `load_project()` 打印轨道类型 | `video, video, subtitle, audio` ✅ |
| 落盘正确 | 直接读 `project.json` | `type=subtitle`、`name=SUB1` ✅ |
| 片段引用一致 | `list_clip_ids()` vs 轨道 `clips[]` | 一致 ✅ |

**未验证（需要重启服务后人工确认）**：

1. 主界面顶栏出现「字幕」按钮
2. 时间轴第 3 行显示为 SUB1，轨头出现「+」按钮
3. 点「字幕」或「+」后：时间轴出现金色字幕块 + 检查器切到字幕编辑页
4. 预览画布按播放头叠字
5. 拖字幕块改时间、拖两边改时长

> ⚠️ **改了 `templates/index.html`，必须重启 Flask**。
> 架构文档第 9.6 条：`debug=False` 时 Jinja 模板会被缓存，
> 改 JS/CSS 不用重启，改 HTML 必须重启。
>
> 当前占用 5173 的进程 PID 为 **14652**。

---

## 五、这次踩到的教训

1. **入口不要绑死在「可选的数据状态」上**
   字幕是全局资源（`project.subtitles[]`），却把唯一入口绑在「轨道类型」上。
   轨道类型是可变的、可被历史数据污染的。顶栏按钮这类**结构性入口**
   应该独立于数据形态存在。

2. **加新功能时同步做存量迁移**
   `make_project()` 早就建 SUB1 了，`normalize()` 却不管老项目。
   `normalize()` 是所有读写路径的必经之地，是最合适的幂等迁移点。

3. **改类型优先「就地改型」而不是「删旧建新」**
   保留主键（track_id / clip_id）能省掉一整套引用修复逻辑。

4. **文案要对得上按钮**
   提示写「+ 字幕」、按钮是「+」，用户会找不着。

---

## 六、遗留问题（未在本次修复内）

| 项 | 说明 |
|---|---|
| 字幕不烧录进视频 | 仍只支持导出 SRT 侧载，需 `subtitles`/`ass` 滤镜 + 字体路径 |
| 字幕无法跨项目复用 | 存在项目级 `subtitles[]`，没有片段库机制 |
| 多客户端删除字幕 | 另一客户端删了字幕，本地 `selectedSubtitleId` 会指向不存在的 id（检查器回落到空状态，不崩） |
| 字幕轨可被删除 | 用户手动删掉 SUB1 轨后，`normalize()` 会在下次读盘时重新补一条（字幕数据不丢，只是轨道重建） |

---

## 附：涉及文件清单

```
core/project.py                     + _ensure_subtitle_track()，normalize() 调用
static/js/main.js                   + import subtitles / addSubtitleFromTopbar()
static/js/state.js                  selectClip() 清 selectedSubtitleId
static/js/inspector.js              空状态文案
templates/index.html                + #btn-subtitle
tools/patch_subtitle_visibility.py  一次性补丁 + 数据迁移（新增）
data/projects/<pid>/project.json    effect 轨 → subtitle 轨
```

<!-- __EOF__ -->