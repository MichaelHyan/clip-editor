# -*- coding: utf-8 -*-
"""字幕前端接线 3：SUB 轨加「+ 字幕」按钮 + 项目设置对话框 + 样式。"""
import io

BASE = "D:/git/clip-editor"


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def sub(s, old, new, label):
    if old not in s:
        if new in s:
            print("  [skip] %s" % label)
            return s
        raise SystemExit("  [FAIL] 锚点缺失: %s" % label)
    print("  [ok]   %s" % label)
    return s.replace(old, new, 1)


# ================================================================ timeline.js
p = BASE + "/static/js/timeline.js"
s = read(p)

# 1) renderHeads 里，subtitle 轨加「+ 字幕」按钮
s = sub(s,
        '''      el("div", { class: "tl-head-btns" }, [
        headBtn("M", "静音", tr.muted, () => toggleTrack(tr.id, "muted")),
        headBtn("H", "隐藏", tr.hidden, () => toggleTrack(tr.id, "hidden")),
        headBtn("L", "锁定", tr.locked, () => toggleTrack(tr.id, "locked")),
      ]),''',
        '''      el("div", { class: "tl-head-btns" },
        tr.type === "subtitle"
          ? [headBtn("+", "在播放头处加字幕", false, addSubtitleHere)]
          : [
              headBtn("M", "静音", tr.muted, () => toggleTrack(tr.id, "muted")),
              headBtn("H", "隐藏", tr.hidden, () => toggleTrack(tr.id, "hidden")),
              headBtn("L", "锁定", tr.locked, () => toggleTrack(tr.id, "locked")),
            ]),''',
        "SUB 轨加 + 字幕按钮")

# 2) 加字幕函数
s = sub(s,
        '''function headBtn(text, title, active, onclick) {''',
        '''async function addSubtitleHere() {
  if (state.playing) setPlaying(false);
  const sub = await subtitles.addSubtitleAtPlayhead("新字幕");
  if (sub) {
    subtitles.selectSubtitle(sub.id);
    bus.emit("toast", { msg: "已添加字幕，在右侧编辑内容", kind: "ok" });
    render();
  }
}

function headBtn(text, title, active, onclick) {''',
        "addSubtitleHere")

# 3) 订阅字幕变更触发重绘
s = sub(s,
        '''  bus.on("timeline:changed", throttle(() => {
    if (drag) return;
    render();
  }, 60));''',
        '''  bus.on("timeline:changed", throttle(() => {
    if (drag || subDrag) return;
    render();
  }, 60));
  bus.on("subtitle:changed", () => { if (!drag && !subDrag) render(); });''',
        "订阅 subtitle:changed 重绘")

write(p, s)

# ================================================================ index.html
p = BASE + "/templates/index.html"
s = read(p)

s = sub(s,
        '    <button class="btn" id="btn-snap" title="创建快照 Ctrl+S">快照</button>',
        '    <button class="btn" id="btn-settings" title="项目设置（分辨率/帧率/格式）">设置</button>\n'
        '    <button class="btn" id="btn-snap" title="创建快照 Ctrl+S">快照</button>',
        "顶栏加设置按钮")

write(p, s)

# ================================================================ CSS
p = BASE + "/static/css/editor.css"
s = read(p)

if ".tl-sub {" not in s:
    marker = "/* __EOF__ */"
    css = '''
/* ================================================================ 字幕 */

.tl-head.type-subtitle { border-left: 3px solid var(--mk); }
.tl-lane.type-subtitle { background: #16140d; }

.tl-sub {
  position: absolute;
  top: 6px;
  bottom: 6px;
  border-radius: 4px;
  background: linear-gradient(180deg, #8a6d1f, #6b5315);
  border: 1px solid rgba(0,0,0,.5);
  box-shadow: 0 1px 3px rgba(0,0,0,.5);
  cursor: grab;
  user-select: none;
  min-width: 6px;
  overflow: hidden;
}
.tl-sub.selected {
  border-color: #fff;
  box-shadow: 0 0 0 1px #fff, 0 0 10px rgba(255,255,255,.35);
  z-index: 4;
}
.tl-sub.dragging-sub { cursor: grabbing; opacity: .85; }
.tl-sub:active { cursor: grabbing; }

.tl-sub-label {
  position: absolute;
  left: 6px; right: 6px; top: 50%;
  transform: translateY(-50%);
  font-size: 11px;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
  text-shadow: 0 1px 2px rgba(0,0,0,.9);
}

.tl-sub-edge {
  position: absolute;
  top: 0; bottom: 0;
  width: 5px;
  cursor: ew-resize;
  z-index: 2;
}
.tl-sub-edge.left { left: 0; }
.tl-sub-edge.right { right: 0; }
.tl-sub:hover .tl-sub-edge { background: rgba(255,255,255,.22); }

/* 字幕编辑面板 */
.insp-textarea {
  width: 100%;
  min-height: 62px;
  padding: 6px 8px;
  border: 1px solid var(--line2);
  border-radius: 6px;
  background: var(--panel2);
  color: var(--txt);
  font: 13px/1.6 "Microsoft YaHei", system-ui, sans-serif;
  resize: vertical;
}
.insp-textarea:focus { outline: none; border-color: var(--acc); }

.insp-btn.danger {
  margin-top: 16px;
  border-color: rgba(255,92,92,.4);
  color: var(--err);
}
.insp-btn.danger:hover { background: rgba(255,92,92,.12); border-color: var(--err); }

/* 项目设置对话框 */
.settings-body {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.settings-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: 10px;
}
.settings-row label { color: var(--dim); font-size: 12px; }
.settings-row .insp-num,
.settings-row .insp-select { width: 100%; text-align: left; height: 26px; }
.settings-pair {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 6px;
  align-items: center;
}
.settings-pair span { color: var(--dim2); font-size: 12px; text-align: center; }
.settings-hint {
  margin-top: 4px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel2);
  color: var(--dim2);
  font-size: 11px;
  line-height: 1.7;
}

/* 字幕编辑面板 */
.insp-textarea {
  width: 100%;
  min-height: 62px;
  padding: 6px 8px;
  border: 1px solid var(--line2);
  border-radius: 6px;
  background: var(--panel2);
  color: var(--txt);
  font: 13px/1.6 "Microsoft YaHei", system-ui, sans-serif;
  resize: vertical;
}
.insp-textarea:focus { outline: none; border-color: var(--acc); }

.insp-btn.danger {
  margin-top: 16px;
  border-color: rgba(255,92,92,.4);
  color: var(--err);
}
.insp-btn.danger:hover { background: rgba(255,92,92,.12); border-color: var(--err); }
'''
    idx = s.rfind(marker)
    s = s[:idx].rstrip() + css + "\n" + marker + "\n"
    print("  [ok]   CSS 字幕 + 设置")
else:
    print("  [skip] CSS")

write(p, s)
print("done")

# __EOF__