# -*- coding: utf-8 -*-
"""批次修复：
1. 素材卡被 flex 压缩（信息区不可见）
2. 线性擦除改全角度（erode_angle 0~360）
3. 项目格式设置入口
"""
import io
import os

BASE = "D:/git/clip-editor"


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def sub(s, old, new, label, optional=False):
    if old not in s:
        if new in s or optional:
            print("  [skip] %s" % label)
            return s
        raise SystemExit("  [FAIL] 锚点缺失: %s" % label)
    print("  [ok]   %s" % label)
    return s.replace(old, new, 1)


# ================================================================ 1. CSS 素材卡
p = BASE + "/static/css/editor.css"
s = read(p)

s = sub(s,
        '''.asset-card {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--panel2);
  overflow: hidden;
  cursor: grab;
  user-select: none;
  transition: border-color .12s, box-shadow .12s;
}''',
        '''.asset-card {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--panel2);
  overflow: hidden;
  cursor: grab;
  user-select: none;
  transition: border-color .12s, box-shadow .12s;
  /* 关键：.assets-body 是 flex column，卡片默认会被压缩，
     导致 .asset-info（名称/截取/按钮）被挤没，只剩缩略图 */
  flex-shrink: 0;
}''',
        "CSS asset-card flex-shrink:0")

# 擦除角度控件样式
if ".insp-angle" not in s:
    marker = "/* __EOF__ */"
    css = '''
/* 擦除角度：滑块 + 角度指示 */
.insp-angle-wrap {
  display: grid;
  grid-template-columns: 1fr 62px 24px;
  gap: 6px;
  align-items: center;
}
'''
    idx = s.rfind(marker)
    s = s[:idx].rstrip() + css + "\n" + marker + "\n"
    print("  [ok]   CSS 角度控件")

write(p, s)

# ================================================================ 2. 全角度擦除
# ---- clip.py: erode_dir -> erode_angle（保留 erode_dir 兼容）
p = BASE + "/core/clip.py"
s = read(p)

s = sub(s,
        '''        "erode": S(0.0),
        "erode_softness": S(0.0),
        "erode_dir": "left",''',
        '''        "erode": S(0.0),
        "erode_softness": S(0.0),
        "erode_angle": S(0.0),   # 0~360 度，0=从左往右擦，90=从上往下''',
        "clip.default_video erode_angle")

s = sub(s,
        '''        elif k in ("blend", "enabled", "erode_dir"):''',
        '''        elif k in ("blend", "enabled", "erode_dir", "erode_angle"):''',
        "clip.normalize 普通字段加 erode_angle（erode_dir 保留兼容）")

# erode_angle 是可关键帧参数
s = sub(s,
        '''    "anchor_x", "anchor_y", "mirror_h", "mirror_v", "erode",
    "erode_softness", "opacity",''',
        '''    "anchor_x", "anchor_y", "mirror_h", "mirror_v", "erode",
    "erode_softness", "opacity",''',
        "clip VIDEO_KF_FIELDS（不变，angle 也走普通 track）",
        optional=True)

# resolve_at 增加 erodeAngle
s = sub(s,
        '''            "erode": kf.eval_track(v.get("erode"), t_local, 0.0),
            "erode_softness": kf.eval_track(v.get("erode_softness"), t_local, 0.0),''',
        '''            "erode": kf.eval_track(v.get("erode"), t_local, 0.0),
            "erode_softness": kf.eval_track(v.get("erode_softness"), t_local, 0.0),
            "erode_angle": kf.eval_track(v.get("erode_angle"), t_local, 0.0),''',
        "clip.resolve_at erode_angle")

write(p, s)

# ---- state.js: 解算 erodeAngle
p = BASE + "/static/js/state.js"
s = read(p)

s = sub(s,
        '''      erode: evalTrack(v.erode, t, 0),
      erodeSoftness: evalTrack(v.erode_softness, t, 0),
      erodeDir: v.erode_dir || "left",''',
        '''      erode: evalTrack(v.erode, t, 0),
      erodeSoftness: evalTrack(v.erode_softness, t, 0),
      erodeAngle: evalTrack(v.erode_angle, t, 0),''',
        "state 解算 erodeAngle")

write(p, s)

# ---- preview.js: applyErode 改角度
p = BASE + "/static/js/preview.js"
s = read(p)

old_erode = s[s.find("// 线性擦除：沿方向从一侧擦掉画面。"):s.find("function drawGrain")]
if not old_erode:
    raise SystemExit("找不到 applyErode 实现")

new_erode = '''// 线性擦除（全角度）：沿角度方向从一侧擦掉画面。
// erode=0 不擦，erode=1 全擦，中间是渐变前沿。
// 用 destination-out + 线性渐变遮罩，比逐像素快几个数量级。
function applyErode(c2d, rp, w, h) {
  const erode = clamp(rp.video.erode || 0, 0, 1);
  if (erode <= 0.001) return;
  const soft = Math.max(0.01, clamp(rp.video.erodeSoftness || 0, 0, 1));
  const deg = ((rp.video.erodeAngle || 0) % 360 + 360) % 360;
  const rad = (deg * Math.PI) / 180;
  const cx = w / 2;
  const cy = h / 2;
  // 投影半径：覆盖画布在该方向上的半长
  const R = (Math.abs(w * Math.cos(rad)) + Math.abs(h * Math.sin(rad))) / 2;
  const dx = Math.cos(rad);
  const dy = Math.sin(rad);
  const x0 = cx - dx * R;
  const y0 = cy - dy * R;
  const x1 = cx + dx * R;
  const y1 = cy + dy * R;

  // 擦除前沿位置（归一化 coord 空间）
  // erode=0 -> front=-soft（全不透明）；erode=1 -> front=1（全透明）
  const front = erode * (1 + soft) - soft;
  const eraseAt = (c) => clamp((c - front) / soft, 0, 1);

  c2d.save();
  c2d.globalCompositeOperation = "destination-out";
  const g = c2d.createLinearGradient(x0, y0, x1, y1);
  const N = 12;
  for (let i = 0; i <= N; i++) {
    const c = i / N;
    g.addColorStop(c, `rgba(0,0,0,${eraseAt(c)})`);
  }
  c2d.fillStyle = g;
  c2d.fillRect(0, 0, w, h);
  c2d.restore();
}

'''
s = s.replace(old_erode, new_erode, 1)
print("  [ok]   preview applyErode 全角度")
write(p, s)

# ---- export.py: geq 用角度投影
p = BASE + "/core/export.py"
s = read(p)

old_ex = s[s.find('        # 线性擦除：用 geq 改写 alpha 通道'):s.find('        if opacity < 0.999:')]
if not old_ex:
    raise SystemExit("找不到 export erode 实现")

new_ex = '''        # 线性擦除（全角度）：用 geq 改写 alpha 通道
        # geq 的 a 输出范围是 0~255（已实测确认，不是 0~1）
        erode = min(max(float(kf.eval_track(v.get("erode"), t0, 0.0) or 0.0), 0.0), 1.0)
        soft = min(max(float(kf.eval_track(v.get("erode_softness"), t0, 0.0) or 0.0), 0.0), 1.0)
        if erode > 0.001:
            import math as _math
            deg = float(kf.eval_track(v.get("erode_angle"), t0, 0.0) or 0.0) % 360.0
            rad = _math.radians(deg)
            ca = _math.cos(rad)
            sa = _math.sin(rad)
            # 投影归一化：coord = ((X-W/2)*cos + (Y-H/2)*sin) / L + 0.5，范围 0~1
            # L = W*|cos| + H*|sin|（保证覆盖整个画布对角线方向）
            L = self.W * abs(ca) + self.H * abs(sa)
            if L < 1e-6:
                L = max(self.W, self.H)
            soft2 = max(soft, 0.01)
            front = erode * (1 + soft2) - soft2
            coord = (f"(((X-{_numf(self.W/2.0, 3)})*{_numf(ca, 9)}"
                     f"+(Y-{_numf(self.H/2.0, 3)})*{_numf(sa, 9)})"
                     f"/{_numf(L, 6)}+0.5)")
            alpha = f"clip(({coord}-{_numf(front, 9)})/{_numf(soft2, 6)},0,1)*255"
            parts.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'")
            if kf.has_keys(v.get("erode")) and self.dynamic != "static":
                self._warn(f"片段 {c.get('name')}：擦除含关键帧，导出取起点值 {erode:g}")

'''
s = s.replace(old_ex, new_ex, 1)
print("  [ok]   export geq 全角度")
write(p, s)

# ---- inspector.js: 擦除方向下拉 -> 角度滑块
p = BASE + "/static/js/inspector.js"
s = read(p)

old_ui = s[s.find('  // 擦除方向（仅在 erode > 0 时有意义'):s.find('  parent.appendChild(el("div", { class: "insp-section-title" }, "镜像"));')]
if not old_ui:
    raise SystemExit("找不到 inspector 擦除方向 UI")

new_ui = '''  // 擦除角度滑块（0~360）
  const angRow = el("div", { class: "insp-param", dataset: { path: "video.erode_angle" } });
  angRow.appendChild(el("label", { class: "insp-label" }, "擦除角度"));
  const angTrack = c.video.erode_angle;
  const angCur = evalTrack(angTrack, localT(), 0);
  const angRange = el("input", {
    type: "range", class: "insp-input", min: 0, max: 360, step: 1, value: angCur,
    dataset: { path: "video.erode_angle", kind: "range" },
  });
  const angNum = el("input", {
    type: "number", class: "insp-num", min: 0, max: 360, step: 1,
    value: Math.round(angCur),
    dataset: { path: "video.erode_angle", kind: "num" },
  });
  const angKf = el("button", {
    class: "insp-kf" + (hasKeys(angTrack) ? " active" : ""),
    dataset: { path: "video.erode_angle" },
    title: "关键帧",
  }, "\\u25C6");
  angRange.addEventListener("input", () => {
    const v = clamp(parseFloat(angRange.value) || 0, 0, 360);
    angNum.value = Math.round(v);
    applyValue(c.id, "video.erode_angle", v);
  });
  angNum.addEventListener("change", () => {
    const v = clamp(parseFloat(angNum.value) || 0, 0, 360);
    angRange.value = v;
    applyValue(c.id, "video.erode_angle", v);
  });
  angKf.addEventListener("click", () => toggleKeyframe(c.id, "video.erode_angle", angTrack));
  angRow.appendChild(angRange);
  angRow.appendChild(angNum);
  angRow.appendChild(angKf);
  parent.appendChild(angRow);

'''
s = s.replace(old_ui, new_ui, 1)
print("  [ok]   inspector 角度滑块")
write(p, s)

print("done")

# __EOF__
