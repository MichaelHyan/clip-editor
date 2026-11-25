# 轨道高度可调 + 双击恢复失效修复

> 修复日期：2026-11-25
> 影响文件：`static/js/timeline.js`、`static/css/editor.css`、
> `tools/verify_track_height.py`（新增）

---

## 一、需求

轨道行高固定 64px，片段密集时（尤其音频波形）看不清。需要能拖动调整高度。

---

## 二、现状

后端 `patch_track` 早就支持 `height`：

```python
allowed = {"name", "muted", "hidden", "locked", "height", "index", "type"}
for k, v in patch.items():
    if k in allowed:
        if k == "height":
            tr[k] = max(28, min(240, int(v)))   # 钳制 28~240
```

轨道头和 lane 也都按 `tr.height` 渲染：

```js
style: { height: (tr.height || 64) + "px" }
```

**缺的只是拖拽把手。**

---

## 三、实现

### 1. 把手位置

```js
// renderHeads() 里，row.appendChild(nameEl) 之后
const rz = el("div", {
  class: "tl-head-resize",
  title: "拖动调整轨道高度（双击恢复默认 64px）",
});
rz.addEventListener("pointerdown", (ev) => onHeadResizeDown(ev, tr, row));
rz.addEventListener("dblclick", (ev) => {
  ev.stopPropagation();
  patchTrackProps(tr.id, { height: 64 }).then(() => render());
});
row.appendChild(rz);
```

```css
.tl-head { position: relative; }   /* 原本没有，把手要绝对定位 */

.tl-head-resize {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;          /* ← 必须 0，不能用负值 */
  height: 5px;
  cursor: ns-resize;
  z-index: 5;
}
.tl-head-resize:hover { background: rgba(76, 141, 255, .55); }
```

**⚠️ 不能用 `bottom: -3px` 做「跨行把手」**：`.tl-head` 是 `overflow: hidden`，
负偏移部分直接被裁掉，把手会消失。只能在行**内部**贴下边缘。

### 2. 拖动逻辑

```js
const MIN_TRACK_H = 28;
const MAX_TRACK_H = 240;

function onHeadResizeDown(ev, tr, row) {
  if (ev.button !== 0) return;
  ev.preventDefault();
  ev.stopPropagation();      // 不要触发整行的拖动排序
  const lane = mCanvas
    ? mCanvas.querySelector('.tl-lane[data-track="' + tr.id + '"]')
    : null;
  const h0 = row.getBoundingClientRect().height;
  headResize = {
    tid: tr.id, tr, row, lane,
    startY: ev.clientY, origH: h0, h: h0, moved: false,
  };
  row.classList.add("resizing-head");
  window.addEventListener("pointermove", onHeadResizeMove);
  window.addEventListener("pointerup", onHeadResizeUp);
}

function onHeadResizeMove(ev) {
  if (!headResize) return;
  const dy = ev.clientY - headResize.startY;
  if (!headResize.moved && Math.abs(dy) < 2) return;
  headResize.moved = true;
  const h = clamp(Math.round(headResize.origH + dy), MIN_TRACK_H, MAX_TRACK_H);
  headResize.h = h;
  // 直接改 DOM，不重建节点（render 在拖动期间被守卫拦住）
  headResize.row.style.height = h + "px";
  if (headResize.lane) headResize.lane.style.height = h + "px";
  // 乐观写本地高度：positionPlayhead() 用 laneTotalHeight() 读 tracks()
  if (headResize.tr) headResize.tr.height = h;
  positionPlayhead();
  syncHeadsScroll();
}

async function onHeadResizeUp() {
  window.removeEventListener("pointermove", onHeadResizeMove);
  window.removeEventListener("pointerup", onHeadResizeUp);
  if (!headResize) return;
  const d = headResize;
  headResize = null;
  if (d.row && d.row.isConnected) d.row.classList.remove("resizing-head");
  if (!d.moved) {
    // 没移动就什么都没变。这里绝不能 render()：
    // 双击 = down/up + down/up + dblclick，第一次 up 就重建节点会让
    // 后续 dblclick 在已游离的旧节点上触发，监听器已消失 -> 双击失效。
    return;
  }
  await patchTrackProps(d.tid, { height: d.h });
  render();
}
```

### 3. 三处同步

拖动中要同时更新**三处**，少一处就不同步：

| 目标 | 代码 | 不同步的后果 |
|---|---|---|
| 轨道头 DOM | `row.style.height = h` | 把手位置不动 |
| 时间轴 lane DOM | `lane.style.height = h` | 轨道头和片段行错位 |
| `tr.height`（内存） | `headResize.tr.height = h` | `laneTotalHeight()` 用旧值算播放头高度 |

`positionPlayhead()` 依赖 `laneTotalHeight()`，而它遍历 `tracks()` 读
`t.height`。不乐观写内存的话，拖动中播放头的竖直长度不跟着变。

### 4. render 守卫

```js
if (drag || headDrag || headResize) return;
```

三个拖拽（片段、轨道排序、轨道高度）都得拦，否则拖动中节点被重建，
`headResize.row` 变游离节点，`style.height` 改了个空气。

---

## 四、踩到的坑：双击恢复失效

### 现象

第一轮验证 14/16，两项失败：

```
[FAIL] 双击恢复 64  -> 28
[FAIL] 恢复也落盘  -> 28
```

拖到下限 28 后双击把手，高度纹丝不动。

### 根因

`onHeadResizeUp` 最初是这样：

```js
if (!d.moved) {
  render();      // ← 问题在这
  return;
}
```

双击的事件序列是：

```
pointerdown → pointerup → pointerdown → pointerup → dblclick
```

**第一次 `pointerup` 就调了 `render()`**，`clear(mHeads)` 把整个轨道头
重建了一遍。后续的 `pointerdown` 打在新节点上，但 `dblclick` 由浏览器
派发给「前后两次点击的公共祖先」——此时旧节点已 `isConnected === false`，
监听器随旧节点一起被回收，`dblclick` 逻辑永远不执行。

### 修复

```js
if (!d.moved) {
  // 没移动就什么都没变。这里绝不能 render()：
  // 双击 = down/up + down/up + dblclick，第一次 up 就重建节点会让
  // 后续 dblclick 在已游离的旧节点上触发，监听器已消失 -> 双击失效。
  return;
}
```

没移动本来就什么都没变，`render()` 纯属多余。删掉即可。

### 教训

**`pointerup` 里无条件 `render()` 会破坏双击、右击菜单、长按等复合手势。**
任何依赖「同一节点上连续多个事件」的交互，都要求节点在序列完成前不被替换。
判断「有没有实际变更」，没变更就什么都不做。

这和架构文档第 9.4 条 `drag.moved` 的坑是同一类问题：**拖拽状态机里，
"没动"应该是零副作用的快路径。**

---

## 五、验证

`tools/verify_track_height.py`（Playwright + 系统 Edge，全程临时项目）：

```
  [OK] 已切到临时项目  -> proj_5ab165943675

== 把手 ==
  [OK] 每条轨道都有高度把手  -> 4 条
  [OK] 把手高度 5px  -> 5
  [OK] 初始行高 64  -> 64

== 向下拖动 ==
  [OK] 行高变大  -> 64 -> 114
  [OK] lane 高度同步  -> lane=114 head=114
  [OK] 增量接近 50px  -> 50

== 落盘 ==
  [OK] height 已落盘  -> disk=114 dom=114
  [OK] 刷新后高度保持  -> reload=114 prev=114

== 向上拖动 ==
  [OK] 行高变小  -> 114 -> 84

== 极值钳制 ==
  [OK] 上限 240  -> 240
  [OK] 下限 28  -> 28

== 双击恢复 ==
  [OK] 双击恢复 64  -> 64
  [OK] 恢复也落盘  -> 64

== 不误触发排序 ==
  [OK] 拖动把手不改顺序  -> ['trk_ve447c3', 'trk_va68c5a'] vs ['trk_ve447c3', 'trk_va68c5a']

== 控制台 ==
  [OK] 控制台无错误

== 汇总：16/16 通过 ==
```

覆盖：把手存在与尺寸、向下/向上拖动、lane 同步、落盘、刷新持久、
28/240 极值钳制、双击恢复、拖动把手不误触发排序、控制台无错误。

---

## 六、相关代码位置

```
static/js/timeline.js
  const MIN_TRACK_H = 28;  MAX_TRACK_H = 240;
  function onHeadResizeDown(ev, tr, row)
  function onHeadResizeMove(ev)
  async function onHeadResizeUp()
  renderHeads() 里 row.appendChild(rz)

static/css/editor.css
  .tl-head { position: relative; }
  .tl-head-resize { ... }
  .tl-head.resizing-head { ... }
```

---

## 七、遗留

| 项 | 说明 |
|---|---|
| 高度不记忆到模板 | 每条轨独立 `height`，新建轨道固定 64。若需要「全局默认轨道高度」可在项目设置里加 |
| 无键盘微调 | 把手只能鼠标拖。可用 `↑/↓` + 修饰键做，但目前没必要 |
| 拖动时无高度数值提示 | 拖动中没有 tooltip 显示当前像素值。行高视觉上足够直观 |

<!-- __EOF__ -->