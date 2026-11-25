# 轨道自由增删 + 两处撤销/测试缺陷修复

> 修复日期：2026-11-25
> 影响文件：`static/js/timeline.js`、`static/css/editor.css`、
> `api/routes_project.py`、`tools/browser_verify_track_ui.py`（重写）、
> `tools/verify_delete_track_undo.py`（新增）

---

## 一、需求：轨道自由增删

之前只能使用新建项目时的默认 4 条轨（V2/V1/SUB1/A1），无法加轨也无法删轨。

### 实现

后端 `POST /tracks`、`DELETE /tracks/<tid>`、`POST /tracks/reorder` 和
`api.js` 的封装**早就齐备**，缺的只是时间轴 UI 入口。

| 位置 | 操作 | index |
|---|---|---|
| 工具栏「+ 视频轨」 | 加一条 video 轨，**置于最上层** | `-1` |
| 工具栏「+ 音频轨」 | 加一条 audio 轨，**置于最下层** | `9999` |
| 轨道头「×」 | 删除该轨道（带片段二次确认） | — |

`HEAD_W` 148 → 172，容纳 4 个按钮（M/H/L/×）不挤掉轨道名。

### 命名规则

`nextTrackName()` 取同类型轨道名末尾数字最大值 +1：

```js
function nextTrackName(type) {
  let maxNum = 0;
  for (const t of tracks()) {
    if (t.type !== type) continue;
    const m = String(t.name || "").match(/(\d+)\s*$/);
    if (m) maxNum = Math.max(maxNum, parseInt(m[1], 10));
  }
  return (type === "audio" ? "A" : "V") + (maxNum + 1);
}
```

V2/V1 → V3，A1 → A2。

### ⚠️ index 语义的坑（重点）

后端 `add_track` 的实现是：

```python
p["tracks"].append(tr)
p["tracks"].sort(key=lambda t: t.get("index", 0))   # 稳定排序
for i, t in enumerate(p["tracks"]): t["index"] = i   # 重编号
```

新轨道是 **append 到末尾**后按 `index` **稳定排序**再重编号。

所以传 `index=0` 时，原有那条 `index=0` 的轨（V2）因为排序稳定会排在前面，
新轨只能排第二。**要让视频轨真正置顶必须传负数（`-1`）**，音频轨传大数
（`9999`）落到底部。

这个坑不实测发现不了——代码看起来完全合理，是稳定排序的语义细节。

### 字幕轨不提供删除按钮

`normalize()` 的 `_ensure_subtitle_track()` 会在下次读盘时自动补一条 SUB 轨，
删了只会让人困惑（删完刷新又出现），且字幕数据不会丢（存在
`project.subtitles[]`，与轨道无关）。所以轨道头对 `type === "subtitle"` 不渲染「×」。

---

## 二、真问题 1：删除轨道后撤销无法恢复片段

### 现象

删除一条带片段的轨道，按 Ctrl+Z 后轨道回来了，但片段永远消失。

### 根因

`api/routes_project.py` 的 `delete_track`：

```python
with ws.lock:
    tr = project_mod.find_track(ws.project, tid)
    if tr is None:
        return C.err("轨道不存在", 404)
    for cid in list(tr.get("clips") or []):
        ws.drop_clip(cid)                       # ← 片段文件从磁盘删掉
    before = copy.deepcopy(ws.project)
    ws.project = project_mod.remove_track(ws.project, tid)
    ...
    ws.commit("删除轨道",
              [{"target": "project", "state": before}],   # ← 只记了 project
              [{"target": "project", "state": after}],
              C.client_id())
```

撤销记录里**只有 project 状态，没有 clip 状态**。`apply_states` 恢复 project 后，
轨道回来了但 `clip_id` 指向已被删除的文件 → 前端 `clip(cid)` 返回 `null` →
轨道显示为空。**删轨道永久不可逆。**

另外撤销栈是**进程内存**（`core/history.py` 的 `_STACKS`），重启服务就全没了。

### 修复

drop 之前先把每个片段的完整快照塞进 undo 列表，redo 里对应 `state: None`，
并逐个 `emit_clip_removed` 让其他客户端同步：

```python
before = copy.deepcopy(ws.project)
clip_ids = list(tr.get("clips") or [])
undo_states = [{"target": "project", "state": before}]
redo_states = []
for cid in clip_ids:
    c = ws.clips.get(cid)
    if c is not None:
        undo_states.append({"target": "clip", "id": cid,
                            "state": copy.deepcopy(c)})
    redo_states.append({"target": "clip", "id": cid, "state": None})
    ws.drop_clip(cid)

ws.project = project_mod.remove_track(ws.project, tid)
ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
ws.save_project()
after = copy.deepcopy(ws.project)

redo_states.insert(0, {"target": "project", "state": after})
ws.commit("删除轨道", undo_states, redo_states, C.client_id())

realtime.emit_project(pid, after, "track_removed", C.client_id())
for cid in clip_ids:
    realtime.emit_clip_removed(pid, cid, C.client_id())
```

`ws.apply_states()` 本来就支持 `target: "clip"`，会把 `state` 非 None 的
片段 `save_clip()` 写回磁盘（包括重建 json 文件）。所以撤销后片段是真回来了。

### 验证

`tools/verify_delete_track_undo.py`（API 级，不依赖浏览器）：

```
临时项目: proj_4dd03685da80
  [OK] 片段已导入  -> clip_1d755c251f65
  [OK] 片段在 state 里
  [OK] 片段挂在轨道上  -> ['clip_1d755c251f65']
  [OK] 删除后轨道不在了
  [OK] 删除后片段也不在了
  [OK] 撤销后轨道回来
  [OK] 撤销后片段内容回来  -> clip_1d755c251f65
  [OK] 片段重新挂在轨道上  -> ['clip_1d755c251f65']
  [OK] 继续撤销后片段消失
== 全部通过 ==
```

**教训：任何「先删数据、后记录撤销」的操作都是错的。撤销快照必须在破坏性
动作之前采集。**

---

## 三、真问题 2：浏览器测试破坏真实数据

### 事故

`tools/browser_verify_track_ui.py` 第一版直接在 `proj_00c13582b005`（用户
当时正在编辑的项目）上跑，最后一步「删除带片段的轨道」**真的删掉了用户的
V1 视频轨和视频片段**。

### 根因

测试脚本读 `localStorage` 里的项目（`boot()` 的逻辑是 `params.get("p") ||
localStorage.getItem(...)`），即用户当前正在用的那个。破坏性测试直接作用在
线上数据上。

### 修复

测试自己建临时项目，显式指定，二次校验，结束时删除：

```python
def create_temp_project():
    d = http("POST", API + "/projects", {"name": "__track_ui_test__"})
    return d["data"]["project"]["id"]

def main():
    temp_pid = create_temp_project()
    try:
        page.goto(BASE + "/?p=" + temp_pid, ...)   # 显式指定，不读 localStorage
        cur_pid = page.evaluate("...state.pid...")
        if cur_pid != temp_pid:
            raise RuntimeError("项目上下文不是临时项目，中止以防破坏真实数据")
        ...  # 所有破坏性操作都在这之上
    finally:
        delete_temp_project(temp_pid)
```

**教训：任何破坏性自动化测试都必须自带隔离环境。** 临时项目、临时目录、
临时端口都行，就是不能用「用户当前正在用的」。

---

## 四、验证

### 浏览器实测（23/23 通过）

```
python tools/browser_verify_track_ui.py
```

```
临时项目: proj_ec90f0cc3410
  [OK] 已切到临时项目  -> proj_ec90f0cc3410

== 基线 ==
  [OK] 初始轨道结构  -> [(0,'video','V2'), (1,'video','V1'), (2,'subtitle','SUB1'), (3,'audio','A1')]
  [OK] 工具栏有「+ 视频轨」
  [OK] 工具栏有「+ 音频轨」

== 加视频轨 ==
  [OK] 轨道数 +1  -> 4 -> 5
  [OK] 新轨在最上层（idx 0）  -> {'idx':0,'type':'video','name':'V3'}
  [OK] 命名 V3
  [OK] index 连续 0..n-1  -> [0,1,2,3,4]

== 加音频轨 ==
  [OK] 轨道数 +1  -> 5 -> 6
  [OK] 新音频轨在最下层  -> {'idx':5,'type':'audio','name':'A2'}
  [OK] 命名 A2

== 删除按钮 ==
  [OK] 轨道头有删除按钮  -> 5
  [OK] 删除按钮数 = 非字幕轨数  -> 5 vs 5

== 删除空轨道 ==
  [OK] 轨道数 -1  -> 6 -> 5
  [OK] 被删轨道不在了

== 带片段轨道：删除与撤销 ==
  [OK] 测试片段已导入  -> clip_72927c922caa
  [OK] 轨道上有 1 个片段
  [OK] 带片段轨道可删除  -> 原 1 个片段
  [OK] 片段已从 state 移除
  [OK] 撤销后轨道回来
  [OK] 撤销后片段也回来  -> clip_72927c922caa
  [OK] 片段重新挂在该轨道上

== 字幕轨保护 ==
  [OK] 字幕轨无删除按钮

== 控制台错误 ==
  无
已删除临时项目: proj_ec90f0cc3410

== 汇总：23/23 通过 ==
```

### API 级验证（9/9 通过）

见上文「真问题 1」的验证输出。

---

## 五、遗留问题

| 项 | 状态 | 说明 |
|---|---|---|
| 轨道不能重命名 | 未实现 | 后端 `PATCH /tracks/<tid>` 支持 `name`，前端没有入口 |
| 轨道不能拖动排序 | 未实现 | 后端 `POST /tracks/reorder` 已就绪，前端没有入口 |
| 撤销栈是进程内存 | 固有限制 | `_STACKS` 在 `core/history.py` 里是模块级字典，重启清空。持久化需序列化整个状态快照 |
| 删除轨道不自动补默认轨 | 设计如此 | 允许删到零视频轨。此时导入片段报「项目中无可用轨道，请先创建轨道」 |
| 轨道类型不可改 | 未实现 | `PATCH /tracks/<tid>` 的 `allowed` 集合里有 `type`，但没有 UI。改类型会影响已有片段的渲染语义（video 轨上的 audio 片段等），需要谨慎设计 |

---

## 六、这次踩到的三个坑

1. **稳定排序的 index 语义**
   `append` + `sort` + 重编号，看起来合理，但传 `index=0` 不会覆盖原有
   首位的轨。要抢首位必须传负数。**这类细节只有实测能发现。**

2. **撤销快照必须在破坏性动作之前**
   `drop_clip()` 之后再记 undo，等于什么都没记。顺序错了，功能就永久残缺。

3. **破坏性测试必须隔离**
   测试脚本跑在用户真实项目上，删了数据才发现。**测试的第一要务是不伤害
   被测系统之外的东西。**

<!-- __EOF__ -->