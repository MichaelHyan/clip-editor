# -*- coding: utf-8 -*-
"""字幕前端接线 2：检查器字幕编辑页 + 项目设置对话框。"""
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


# ================================================================ inspector.js
p = BASE + "/static/js/inspector.js"
s = read(p)

# 1) 导入字幕模块
s = sub(s,
        'import { evalTrack, hasKeys, allTimes } from "./keyframe.js";',
        'import { evalTrack, hasKeys, allTimes } from "./keyframe.js";\n'
        'import * as subtitles from "./subtitles.js";',
        "inspector 导入字幕模块")

# 2) build() 优先判断是否选中字幕
s = sub(s,
        '''function build() {
  clear(root);
  const c = selectedClip();
  if (!c) {
    root.appendChild(el("div", { class: "insp-empty" }, "未选中片段\\n在时间轴上点选一个片段以编辑属性"));
    return;
  }''',
        '''function build() {
  clear(root);

  // 字幕优先：选中字幕时显示字幕编辑页
  const sub = state.selectedSubtitleId
    ? (subtitles.listSubtitles().find((x) => x.id === state.selectedSubtitleId) || null)
    : null;
  if (sub) {
    buildSubtitleEditor(sub);
    return;
  }

  const c = selectedClip();
  if (!c) {
    root.appendChild(el("div", { class: "insp-empty" },
      "未选中片段\\n在时间轴上点选一个片段以编辑属性\\n\\n要加字幕：点 SUB 轨旁的「+ 字幕」"));
    return;
  }''',
        "build 优先字幕")

# 3) 字幕编辑页实现
s = sub(s,
        '''// 片段改名：就地输入框，回车/失焦提交，Esc 取消''',
        '''// ---------------------------------------------------------------- 字幕编辑

function buildSubtitleEditor(sub) {
  const st = sub.style || {};
  const head = el("div", { class: "insp-header" }, [
    el("div", { class: "insp-title" }, "字幕"),
    el("div", { class: "insp-sub" },
      `${sub.start.toFixed(2)}s ~ ${sub.end.toFixed(2)}s · ${(sub.end - sub.start).toFixed(2)}s`),
  ]);
  root.appendChild(head);
  root.appendChild(el("div", { class: "insp-tabs" }, [
    el("button", { class: "insp-tab active" }, "字幕"),
  ]));

  const body = el("div", { class: "insp-body" });

  // 文本
  body.appendChild(el("div", { class: "insp-section-title" }, "内容"));
  const ta = el("textarea", { class: "insp-textarea", rows: "3",
    placeholder: "输入字幕文字，支持换行" });
  ta.value = sub.text || "";
  let saveTimer = null;
  const doSave = async () => {
    const txt = ta.value;
    if (txt === (sub.text || "")) return;
    await subtitles.patchSubtitle(sub.id, { text: txt });
    build();
  };
  ta.addEventListener("input", () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(doSave, 700);
  });
  ta.addEventListener("blur", doSave);
  ta.addEventListener("keydown", (ev) => ev.stopPropagation());
  body.appendChild(ta);

  // 时间
  body.appendChild(el("div", { class: "insp-section-title" }, "时间"));
  body.appendChild(timeRow("起点", sub.start, async (v) => {
    await subtitles.patchSubtitle(sub.id, { start: v });
    build();
  }));
  body.appendChild(timeRow("终点", sub.end, async (v) => {
    await subtitles.patchSubtitle(sub.id, { end: v });
    build();
  }));

  // 样式
  body.appendChild(el("div", { class: "insp-section-title" }, "样式"));
  body.appendChild(styleRow("字号", "number", st.fontSize, { min: 8, max: 200, step: 1 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { fontSize: v } }); build(); }));
  body.appendChild(styleRow("颜色", "color", st.color || "#ffffff", {},
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { color: v } }); build(); }));
  body.appendChild(styleRow("描边色", "color", st.outlineColor || "#000000", {},
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { outlineColor: v } }); build(); }));
  body.appendChild(styleRow("描边宽", "number", st.outline, { min: 0, max: 12, step: 0.5 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { outline: v } }); build(); }));
  body.appendChild(styleRow("水平位置", "number", st.x != null ? st.x : 0.5,
    { min: 0, max: 1, step: 0.01 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { x: v } }); build(); }));
  body.appendChild(styleRow("垂直位置", "number", st.y != null ? st.y : 0.86,
    { min: 0, max: 1, step: 0.01 },
    async (v) => { await subtitles.patchSubtitle(sub.id, { style: { y: v } }); build(); }));

  // 对齐
  const alignRow = el("div", { class: "insp-param" });
  alignRow.appendChild(el("label", { class: "insp-label" }, "对齐"));
  const alignSel = el("select", { class: "insp-select" });
  for (const [v, l] of [["left", "左"], ["center", "中"], ["right", "右"]]) {
    const o = el("option", { value: v }, l);
    if ((st.align || "center") === v) o.selected = true;
    alignSel.appendChild(o);
  }
  alignSel.addEventListener("change", async () => {
    await subtitles.patchSubtitle(sub.id, { style: { align: alignSel.value } });
    build();
  });
  alignRow.appendChild(alignSel);
  body.appendChild(alignRow);

  // 删除
  const delBtn = el("button", { class: "insp-btn danger" }, "删除此字幕");
  delBtn.addEventListener("click", async () => {
    if (!confirm("删除这条字幕？")) return;
    await subtitles.removeSubtitle(sub.id);
    build();
  });
  body.appendChild(delBtn);

  root.appendChild(body);
}

function timeRow(label, value, onCommit) {
  const row = el("div", { class: "insp-param" });
  row.appendChild(el("label", { class: "insp-label" }, label));
  const num = el("input", {
    type: "number", class: "insp-num", min: 0, step: 0.05,
    value: Math.round(value * 100) / 100,
  });
  num.addEventListener("change", () => onCommit(Math.max(0, Number(num.value) || 0)));
  num.addEventListener("keydown", (ev) => ev.stopPropagation());
  row.appendChild(num);
  const btn = el("button", { class: "insp-kf", title: "取播放头" }, "\\u23F1");
  btn.addEventListener("click", () => {
    const t = Math.round(state.playhead * 100) / 100;
    num.value = t;
    onCommit(t);
  });
  row.appendChild(btn);
  return row;
}

function styleRow(label, type, value, opts, onCommit) {
  const row = el("div", { class: "insp-param" });
  row.appendChild(el("label", { class: "insp-label" }, label));
  const inp = el("input", {
    type, class: type === "color" ? "insp-color" : "insp-num",
    ...(opts || {}),
  });
  inp.value = value != null ? value : "";
  inp.addEventListener("change", () => {
    const v = type === "color" ? inp.value : Number(inp.value);
    if (type === "number" && !Number.isFinite(v)) return;
    onCommit(v);
  });
  inp.addEventListener("keydown", (ev) => ev.stopPropagation());
  row.appendChild(inp);
  return row;
}

// 片段改名：就地输入框，回车/失焦提交，Esc 取消''',
        "字幕编辑页")

# 4) selection:changed 时刷新（已有 build，但 subtitle 选中走不同事件）
s = sub(s,
        '''  bus.on("selection:changed", () => { if (!suppress) build(); });''',
        '''  bus.on("selection:changed", () => { if (!suppress) build(); });
  bus.on("subtitle:changed", () => { if (!suppress) build(); });''',
        "订阅 subtitle:changed")

write(p, s)
print("done")

# __EOF__