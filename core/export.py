# -*- coding: utf-8 -*-
"""导出管线：把项目 + 片段编译成一条 ffmpeg filter_complex 命令。

## 语义对齐
导出与前端预览共用 core.keyframe 求值；变换顺序 trim -> crop -> 镜像 -> scale -> 效果 -> 透明度 -> 定位。

## 关键帧导出策略
- expr：拟合成 ffmpeg 时间表达式（含逗号的分段式一律用单引号包裹，避免被当作滤镜分隔符）
  支持：位置、旋转、亮度、色相、响度等
- static：全部取起点值

## 输入复用
每个 clip 只 -i 一次，视频链与音频链共享同一输入索引。
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import clip as clip_mod
from . import config as cfg
from . import keyframe as kf
from . import media as media_mod
from . import realtime

FFMPEG = media_mod.FFMPEG
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _num(v: Any) -> str:
    if v is None:
        return "0"
    if isinstance(v, bool):
        return "1" if v else "0"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _numf(v: Any, d: int = 6) -> str:
    try:
        return f"{float(v):.{d}f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return "0"


def track_expr(track: Any, t0: float, t1: float, tv: str = "t",
               keys_per_ease: int = 3) -> str:
    """把关键帧曲线拟合成关于 tv 的分段线性表达式（返回值可能含逗号，调用方须用引号包裹）。"""
    if not kf.has_keys(track):
        return _num(kf.eval_track(track, t0, 0))

    keys = sorted((dict(k) for k in track.get("keys") or []),
                  key=lambda k: float(k.get("t", 0.0)))
    if not keys:
        return _num(kf.eval_track(track, t0, 0))

    pts: List[Tuple[float, Any]] = []

    def add(t: float, v: Any) -> None:
        if pts and abs(pts[-1][0] - t) < 1e-6:
            pts[-1] = (t, v)
        else:
            pts.append((t, v))

    if float(keys[0]["t"]) > t0 + 1e-6:
        add(t0, kf.eval_track(track, t0))

    for i, k in enumerate(keys):
        t = float(k.get("t", 0.0))
        if t < t0 - 1e-6 or t > t1 + 1e-6:
            continue
        add(t, k.get("v"))
        if i + 1 < len(keys):
            nk = keys[i + 1]
            span = float(nk.get("t", 0.0)) - t
            if span > 1e-6 and k.get("e", "linear") not in ("linear", "hold"):
                for j in range(1, keys_per_ease + 1):
                    tt = t + span * j / (keys_per_ease + 1)
                    if t0 - 1e-6 <= tt <= t1 + 1e-6:
                        add(tt, kf.eval_track(track, tt))

    last_t = float(keys[-1]["t"])
    if last_t < t1 - 1e-6:
        add(t1, kf.eval_track(track, t1))

    if not pts:
        return _num(kf.eval_track(track, t0))
    if len(pts) == 1:
        return _num(pts[0][1])

    def seg(va: Any, vb: Any, ta: float, tb: float) -> str:
        if abs(tb - ta) < 1e-9:
            return _num(vb)
        slope = (float(vb) - float(va)) / (tb - ta)
        return f"({_num(va)}+{_numf(slope, 9)}*({tv}-{_numf(ta, 6)}))"

    expr = _num(pts[-1][1])
    for i in range(len(pts) - 2, -1, -1):
        ta, va = pts[i]
        tb, vb = pts[i + 1]
        expr = f"if(lt({tv},{_numf(tb, 6)}),{seg(va, vb, ta, tb)},{expr})"
    return expr


def atempo_chain(speed: float) -> List[str]:
    try:
        s = float(speed)
    except (TypeError, ValueError):
        s = 1.0
    if s <= 0:
        s = 1.0
    parts: List[str] = []
    guard = 0
    while s > 2.0 + 1e-9 and guard < 12:
        parts.append("atempo=2.0")
        s /= 2.0
        guard += 1
    while s < 0.5 - 1e-9 and guard < 24:
        parts.append("atempo=0.5")
        s /= 0.5
        guard += 1
    if abs(s - 1.0) > 1e-6:
        parts.append(f"atempo={_numf(s, 6)}")
    return parts


def pitch_ratio(semitones: float) -> float:
    try:
        return 2.0 ** (float(semitones) / 12.0)
    except (TypeError, ValueError):
        return 1.0


class Graph:
    def __init__(self, project: Dict[str, Any], clips: Dict[str, Dict[str, Any]],
                 opts: Optional[Dict[str, Any]] = None) -> None:
        self.project = project
        self.clips = clips
        self.opts = opts or {}
        st = project.get("settings") or {}
        self.W = int(self.opts.get("width") or st.get("width") or 1920)
        self.H = int(self.opts.get("height") or st.get("height") or 1080)
        self.fps = float(self.opts.get("fps") or st.get("fps") or 60)
        self.sr = int(self.opts.get("sample_rate") or st.get("sample_rate") or 48000)
        self.dynamic = str(self.opts.get("dynamic", "expr"))
        self.warnings: List[str] = []
        self.inputs: List[List[str]] = []
        self.chains: List[str] = []
        self.clip_input: Dict[str, int] = {}
        self._pos: Dict[str, Dict[str, str]] = {}
        self.audio_parts: List[str] = []

    # ------------------------------------------------------------ 基础

    def total_duration(self) -> float:
        end = 0.0
        for tr in self.project.get("tracks", []):
            for cid in tr.get("clips") or []:
                c = self.clips.get(cid)
                if not c:
                    continue
                st = float((c.get("timeline") or {}).get("start") or 0.0)
                end = max(end, st + (clip_mod.duration_of(c) or 0.0))
        return max(end, float(self.opts.get("min_duration") or 0.0))

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _alloc_input(self, c: Dict[str, Any]) -> Optional[int]:
        """每个 clip 只分配一次输入，返回输入索引。"""
        cid = c.get("id")
        if cid in self.clip_input:
            return self.clip_input[cid]
        media = c.get("media") or {}
        path = media.get("path")
        if not path or not os.path.isfile(path):
            self._warn(f"片段 {c.get('name')} 源文件缺失，已跳过")
            return None
        kind = media.get("kind")
        if kind == "image":
            dur = clip_mod.duration_of(c) or 5.0
            spd = kf.eval_track((c.get("video") or {}).get("speed"), 0.0, 1.0) or 1.0
            hold = dur * max(1.0, spd) + 1.0
            self.inputs.append(["-loop", "1", "-t", _numf(hold, 6), "-i", path])
        else:
            self.inputs.append(["-i", path])
        idx = len(self.inputs) - 1
        self.clip_input[cid] = idx
        return idx

    # ------------------------------------------------------------ 主流程

    def build(self) -> Dict[str, Any]:
        # BUILD_IDEMPOTENT
        # 每次 build 前重置累积状态，否则重复调用会把滤镜链追加两遍。
        # （preview-cmd 会先 build 再 command，而 command 内部又 build 一次）
        self.inputs = []
        self.chains = []
        self.clip_input = {}
        self._pos = {}
        self.audio_parts = []
        self.warnings = []

        total = self.total_duration()
        if total <= 1e-3:
            raise ValueError("时间轴为空，没有可导出的内容")

        tracks = sorted(self.project.get("tracks", []), key=lambda t: t.get("index", 0))

        # 视频层：index 大=底层，先画底层 -> 按 index 降序
        video_tracks = [t for t in tracks
                        if t.get("type") in ("video", "effect") and not t.get("hidden")]
        video_tracks.sort(key=lambda t: t.get("index", 0), reverse=True)

        # 音频层：video + audio 轨都出声（去重）
        audio_tracks = [t for t in tracks
                        if t.get("type") in ("audio", "video") and not t.get("muted")]

        # 预分配所有需要的输入
        need: List[Dict[str, Any]] = []
        seen = set()
        for tr in video_tracks + audio_tracks:
            for cid in tr.get("clips") or []:
                c = self.clips.get(cid)
                if c and cid not in seen:
                    seen.add(cid)
                    need.append(c)
        for c in need:
            self._alloc_input(c)

        # 构建视频链（层序：底层先 overlay）
        layers: List[Tuple[int, str, float, float]] = []
        for layer_i, tr in enumerate(video_tracks):
            for cid in tr.get("clips") or []:
                c = self.clips.get(cid)
                if not c or (c.get("media") or {}).get("kind") == "audio":
                    continue
                r = self._video_chain(c)
                if r:
                    label, start, end = r
                    layers.append((layer_i, label, start, end))

        base = (f"color=c={(self.project.get('settings') or {}).get('background', 'black')}"
                f":s={self.W}x{self.H}:r={_numf(self.fps, 6)}:d={_numf(total, 6)}[vbase0]")
        self.chains.append(base)
        cur = "vbase0"
        # layer_i 小 = 底层 -> 先叠；layer_i 大 = 上层 -> 后叠
        layers.sort(key=lambda x: x[0])
        for n, (_, label, start, end) in enumerate(layers):
            out = f"vstk{n}"
            px = self._pos.get(label, {}).get("x", "0")
            py = self._pos.get(label, {}).get("y", "0")
            self.chains.append(
                f"[{cur}][{label}]overlay="
                f"x='main_w/2-overlay_w/2+({px})':"
                f"y='main_h/2-overlay_h/2+({py})':"
                f"enable='between(t,{_numf(start, 6)},{_numf(end, 6)})':"
                f"eof_action=pass:format=auto[{out}]"
            )
            cur = out
        self.chains.append(f"[{cur}]format=yuv420p[vout]")

        # 音频链
        a_labels: List[str] = []
        done = set()
        for tr in audio_tracks:
            for cid in tr.get("clips") or []:
                if cid in done:
                    continue
                done.add(cid)
                c = self.clips.get(cid)
                if not c:
                    continue
                label = self._audio_chain(c)
                if label:
                    a_labels.append(label)

        if a_labels:
            if len(a_labels) == 1:
                self.chains.append(f"[{a_labels[0]}]anull[aout]")
            else:
                mix = "".join(f"[{l}]" for l in a_labels)
                self.chains.append(
                    f"{mix}amix=inputs={len(a_labels)}:normalize=0:dropout_transition=0[aout]")
        else:
            self.chains.append(
                f"anullsrc=r={self.sr}:cl=stereo,atrim=0:{_numf(total, 6)}[aout]")

        return {
            "inputs": self.inputs,
            "filter": ";\n".join(self.chains),
            "total_duration": total,
            "warnings": self.warnings,
            "width": self.W, "height": self.H, "fps": self.fps,
        }

    # ------------------------------------------------------------ 视频链

    def _video_chain(self, c: Dict[str, Any]) -> Optional[Tuple[str, float, float]]:
        cid = c.get("id")
        inp = self.clip_input.get(cid)
        if inp is None:
            return None
        v = c.get("video") or {}
        if v.get("enabled") is False:
            return None
        media = c.get("media") or {}
        kind = media.get("kind")
        start = float((c.get("timeline") or {}).get("start") or 0.0)
        dur = clip_mod.duration_of(c) or 0.0
        if dur <= 1e-6:
            return None
        end = start + dur
        t0, t1 = 0.0, dur

        src_in = float(kf.eval_track(v.get("src_in"), t0, 0.0) or 0.0)
        src_out = kf.eval_track(v.get("src_out"), t0, None)
        if src_out is None:
            src_out = (media.get("probe") or {}).get("duration") or (src_in + dur)
        speed = float(kf.eval_track(v.get("speed"), t0, 1.0) or 1.0)
        if speed <= 0:
            speed = 1.0
        if self.dynamic != "static" and kf.has_keys(v.get("speed")):
            self._warn(f"片段 {c.get('name')}：倍速含关键帧，导出取起点值 {speed:g}x")

        parts: List[str] = []
        if kind != "image":
            parts.append(f"trim=start={_numf(src_in, 6)}:end={_numf(float(src_out), 6)}")
        parts.append("setpts=PTS-STARTPTS")
        if abs(speed - 1.0) > 1e-6:
            parts.append(f"setpts=PTS/{_numf(speed, 6)}")

        crop = v.get("crop") or {}
        cl = float(kf.eval_track(crop.get("left"), t0, 0.0) or 0.0)
        cr = float(kf.eval_track(crop.get("right"), t0, 0.0) or 0.0)
        ct = float(kf.eval_track(crop.get("top"), t0, 0.0) or 0.0)
        cb = float(kf.eval_track(crop.get("bottom"), t0, 0.0) or 0.0)
        if any(x > 1e-6 for x in (cl, cr, ct, cb)):
            cl, cr, ct, cb = [min(max(x, 0.0), 0.95) for x in (cl, cr, ct, cb)]
            wf = max(0.02, 1.0 - cl - cr)
            hf = max(0.02, 1.0 - ct - cb)
            parts.append(
                f"crop=trunc(iw*{_numf(wf, 6)}/2)*2:trunc(ih*{_numf(hf, 6)}/2)*2"
                f":trunc(iw*{_numf(cl, 6)}/2)*2:trunc(ih*{_numf(ct, 6)}/2)*2")
            if self.dynamic != "static" and any(kf.has_keys(crop.get(f))
                                               for f in ("left", "right", "top", "bottom")):
                self._warn(f"片段 {c.get('name')}：裁剪含关键帧，导出取起点值")

        if bool(kf.eval_track(v.get("mirror_h"), t0, False)):
            parts.append("hflip")
        if bool(kf.eval_track(v.get("mirror_v"), t0, False)):
            parts.append("vflip")

        scale = float(kf.eval_track(v.get("scale"), t0, 1.0) or 1.0)
        # 先等比缩放到画布（与预览 applyTransform 的 fit 一致）：
        # fit = min(W/iw, H/ih)，再用目标分辨率写死表达式
        # 不缩放的话，852x480 的源会以原尺寸 overlay 到 1920x1080 中央，只占一小块
        fit_expr = f"min({self.W}/iw,{self.H}/ih)"
        parts.append(
            f"scale=w='trunc(iw*{fit_expr}/2)*2'"
            f":h='trunc(ih*{fit_expr}/2)*2':flags=lanczos")

        # 用户的 scale 在此基础上再乘（与预览语义一致）
        if abs(scale - 1.0) > 1e-6:
            s = min(max(scale, 0.01), 8.0)
            parts.append(f"scale=trunc(iw*{_numf(s, 6)}/2)*2:trunc(ih*{_numf(s, 6)}/2)*2:flags=lanczos")
            if self.dynamic != "static" and kf.has_keys(v.get("scale")):
                self._warn(f"片段 {c.get('name')}：缩放含关键帧，导出取起点值 {scale:g}")

        fx_filters, fx_warn = self._effect_filters(c, t0)
        parts.extend(fx_filters)
        for w in fx_warn:
            self._warn(w)

        opacity = min(max(float(kf.eval_track(v.get("opacity"), t0, 1.0)), 0.0), 1.0)
        parts.append("format=rgba")

        # 线性擦除（全角度）：用 geq 改写 alpha 通道
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

        if opacity < 0.999:
            parts.append(f"colorchannelmixer=aa={_numf(opacity, 6)}")
            if kf.has_keys(v.get("opacity")) and self.dynamic != "static":
                self._warn(f"片段 {c.get('name')}：透明度含关键帧，导出取起点值 {opacity:g}")

        label = f"v{len(self._pos)}"
        self.chains.append(f"[{inp}:v]" + ",".join(parts) + f"[{label}]")

        tv = f"(t-{_numf(start, 6)})"
        pxt, pyt = v.get("pos_x"), v.get("pos_y")
        if self.dynamic != "static" and (kf.has_keys(pxt) or kf.has_keys(pyt)):
            px = track_expr(pxt, t0, t1, tv)
            py = track_expr(pyt, t0, t1, tv)
        else:
            px = _num(kf.eval_track(pxt, t0, 0.0))
            py = _num(kf.eval_track(pyt, t0, 0.0))
            if self.dynamic != "static" and (kf.has_keys(pxt) or kf.has_keys(pyt)):
                self._warn(f"片段 {c.get('name')}：位置含关键帧但 dynamic=static，取起点值")

        self._pos[label] = {"x": px, "y": py}
        return label, start, end

    def _effect_filters(self, c: Dict[str, Any], t0: float) -> Tuple[List[str], List[str]]:
        out: List[str] = []
        warns: List[str] = []
        name = c.get("name")
        dur = clip_mod.duration_of(c) or t0 + 1.0
        for fx in c.get("effects") or []:
            if fx.get("enabled") is False:
                continue
            et = fx.get("type")
            p = fx.get("params") or {}

            def val(k, d=0.0):
                return float(kf.eval_track(p.get(k), t0, d) or 0.0)

            def expr_of(k, d=0.0):
                return track_expr(p.get(k), t0, dur, "t")

            def has_kf(k):
                return kf.has_keys(p.get(k))

            if et == "blur":
                r = val("radius")
                if r > 0.01:
                    out.append(f"gblur=sigma={_numf(min(r, 200), 6)}")
                if has_kf("radius"):
                    warns.append(f"片段 {name}：模糊半径含关键帧，导出取起点值")
            elif et == "brightness":
                out.append(f"eq=brightness={_numf(val('value'), 6)}")
                if has_kf("value"):
                    warns.append(f"片段 {name}：亮度含关键帧，导出取起点值")
            elif et == "contrast":
                out.append(f"eq=contrast={_numf(val('value', 1.0), 6)}")
                if has_kf("value"):
                    warns.append(f"片段 {name}：对比度含关键帧，导出取起点值")
            elif et == "saturation":
                out.append(f"eq=saturation={_numf(val('value', 1.0), 6)}")
                if has_kf("value"):
                    warns.append(f"片段 {name}：饱和度含关键帧，导出取起点值")
            elif et == "hue":
                if has_kf("degrees"):
                    out.append(f"hue=h='{expr_of('degrees')}'")
                else:
                    out.append(f"hue=h={_numf(val('degrees'), 6)}")
            elif et == "grayscale":
                a = min(max(val("amount", 1.0), 0.0), 1.0)
                if a > 0.001:
                    out.append(f"hue=s={_numf(1.0 - a, 6)}")
                if has_kf("amount"):
                    warns.append(f"片段 {name}：去色强度含关键帧，导出取起点值")
            elif et == "sharpen":
                a = val("amount")
                if a > 0.001:
                    out.append(f"unsharp=5:5:{_numf(min(a, 10), 6)}:5:5:0")
                if has_kf("amount"):
                    warns.append(f"片段 {name}：锐化含关键帧，导出取起点值")
            elif et == "vignette":
                out.append(f"vignette=angle={_numf(max(val('angle', 0.6), 0.0), 6)}")
                if has_kf("angle"):
                    warns.append(f"片段 {name}：暗角含关键帧，导出取起点值")
            else:
                spec = None
                try:
                    from . import effects as effects_mod
                    spec = effects_mod.get_effect(et)
                except Exception:
                    spec = None
                tpl = ((fx.get("meta") or {}).get("ffmpeg_template")
                       or ((spec or {}).get("ffmpeg") or {}).get("template") or "")
                if tpl:
                    kwargs = {}
                    for k in ((spec or {}).get("params") or p).keys():
                        try:
                            kwargs[k] = _numf(val(k), 6)
                        except Exception:
                            kwargs[k] = "0"
                    try:
                        frag = tpl.format(**kwargs)
                    except (KeyError, IndexError, ValueError):
                        frag = ""
                    if frag:
                        out.append(frag)
                    else:
                        warns.append(f"片段 {name}：效果 {et} 模板无法求值，已跳过")
                else:
                    warns.append(f"片段 {name}：效果 {et} 无导出模板，已跳过")
        return out, warns

    # ------------------------------------------------------------ 音频链

    def _audio_chain(self, c: Dict[str, Any]) -> Optional[str]:
        cid = c.get("id")
        inp = self.clip_input.get(cid)
        if inp is None:
            return None
        media = c.get("media") or {}
        kind = media.get("kind")
        probe = media.get("probe") or {}
        if kind == "image":
            return None
        if kind != "audio" and not probe.get("has_audio"):
            return None
        a = c.get("audio") or {}
        if a.get("enabled") is False:
            return None
        start = float((c.get("timeline") or {}).get("start") or 0.0)
        dur = clip_mod.duration_of(c) or 0.0
        if dur <= 1e-6:
            return None
        t0, t1 = 0.0, dur

        src_in = float(kf.eval_track(a.get("src_in"), t0, 0.0) or 0.0)
        src_out = kf.eval_track(a.get("src_out"), t0, None)
        if src_out is None:
            src_out = probe.get("duration") or (src_in + dur)
        src_out = max(float(src_out), src_in + 0.05)

        parts: List[str] = [f"atrim=start={_numf(src_in, 6)}:end={_numf(src_out, 6)}",
                            "asetpts=PTS-STARTPTS"]

        speed = float(kf.eval_track(a.get("speed"), t0, 1.0) or 1.0)
        if speed <= 0:
            speed = 1.0
        parts.extend(atempo_chain(speed))
        if kf.has_keys(a.get("speed")) and self.dynamic != "static":
            self._warn(f"片段 {c.get('name')}：音频倍速含关键帧，导出取起点值 {speed:g}x")

        pitch = float(kf.eval_track(a.get("pitch"), t0, 0.0) or 0.0)
        if abs(pitch) > 1e-6:
            r = pitch_ratio(pitch)
            if 0.1 < r < 10:
                parts.append(f"rubberband=pitch={_numf(r, 6)}")
            if kf.has_keys(a.get("pitch")):
                self._warn(f"片段 {c.get('name')}：音调含关键帧，导出取起点值")

        gain = float(kf.eval_track(a.get("gain"), t0, 0.0) or 0.0)
        if kf.has_keys(a.get("gain")) and self.dynamic != "static":
            parts.append(f"volume='{track_expr(a.get('gain'), t0, t1, 't')}dB':eval=frame")
        elif abs(gain) > 1e-6:
            parts.append(f"volume={_numf(gain, 6)}dB")

        pan = float(kf.eval_track(a.get("pan"), t0, 0.0) or 0.0)
        if abs(pan) > 1e-6:
            pv = min(max(pan, -1.0), 1.0)
            l = _numf(1.0 - max(0.0, pv), 4)
            r = _numf(1.0 - max(0.0, -pv), 4)
            parts.append(f"pan=stereo|c0={l}*c0|c1={r}*c1")

        fade_in = float(kf.eval_track(a.get("fade_in"), t0, 0.0) or 0.0)
        fade_out = float(kf.eval_track(a.get("fade_out"), t0, 0.0) or 0.0)
        if fade_in > 1e-3:
            parts.append(f"afade=t=in:st=0:d={_numf(min(fade_in, dur), 6)}")
        if fade_out > 1e-3:
            fd = min(fade_out, dur)
            parts.append(f"afade=t=out:st={_numf(max(0.0, dur - fd), 6)}:d={_numf(fd, 6)}")

        if start > 1e-6:
            ms = int(round(start * 1000))
            parts.append(f"adelay={ms}|{ms}")

        label = f"a{len(self.audio_parts)}"
        self.audio_parts.append(label)
        self.chains.append(f"[{inp}:a]" + ",".join(parts) + f"[{label}]")
        return label

    # ------------------------------------------------------------ 命令行

    def command(self, out_path: str, enc: Dict[str, Any]) -> List[str]:
        g = self.build()
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "info", "-stats"]
        for args in g["inputs"]:
            cmd.extend(args)
        cmd.extend(["-filter_complex", g["filter"], "-map", "[vout]"])
        if "[aout]" in g["filter"]:
            cmd.extend(["-map", "[aout]"])
        cmd.extend([
            "-r", _numf(self.fps, 6),
            "-c:v", enc.get("vcodec", "libx264"),
            "-preset", enc.get("preset", "medium"),
            "-crf", str(enc.get("crf", 20)),
            "-pix_fmt", enc.get("pix_fmt", "yuv420p"),
        ])
        if enc.get("vbitrate"):
            cmd.extend(["-b:v", str(enc["vbitrate"])])
        if "[aout]" in g["filter"]:
            cmd.extend(["-c:a", "aac", "-b:a", str(enc.get("abitrate", "192k")),
                        "-ar", str(self.sr), "-ac", "2"])
        cmd.extend(["-movflags", "+faststart", out_path])
        return cmd


# ---------------------------------------------------------------- 任务管理

_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_ORDER: List[str] = []
_MAX_JOBS = 40


def exports_root() -> str:
    p = os.path.join(cfg.ROOT, "data", "exports").replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def job_dir(jid: str) -> str:
    p = os.path.join(exports_root(), jid).replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["id"], "project_id": job.get("project_id"),
        "status": job.get("status"), "progress": round(job.get("progress", 0.0), 4),
        "started": job.get("started"), "finished": job.get("finished"),
        "output": job.get("output"), "size": job.get("size", 0),
        "duration": job.get("duration", 0.0), "error": job.get("error"),
        "warnings": job.get("warnings", []), "encoder": job.get("encoder", {}),
        "speed": job.get("speed"), "eta": job.get("eta"),
    }


def get_job(jid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        j = _JOBS.get(jid)
        return _public(j) if j else None


def list_jobs(pid: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        out = [_public(_JOBS[j]) for j in _ORDER if j in _JOBS]
    if pid:
        out = [x for x in out if x.get("project_id") == pid]
    return sorted(out, key=lambda x: x.get("started") or 0, reverse=True)


def cancel_job(jid: str) -> bool:
    with _LOCK:
        job = _JOBS.get(jid)
        if not job or job.get("status") not in ("queued", "running"):
            return False
        job["cancel"] = True
        proc = job.get("proc")
    if proc:
        for fn in (proc.terminate, proc.kill):
            try:
                fn()
            except OSError:
                pass
    return True


def _update(jid: str, **kw: Any) -> None:
    with _LOCK:
        job = _JOBS.get(jid)
        if not job:
            return
        job.update(kw)
        snap = _public(job)
    try:
        realtime.broadcast_agent_event("export.progress", snap)
    except Exception:
        pass


def start_export(project: Dict[str, Any], clips: Dict[str, Dict[str, Any]],
                 opts: Dict[str, Any]) -> Dict[str, Any]:
    jid = "exp_" + uuid.uuid4().hex[:10]
    enc = {
        "vcodec": "libx264",
        "preset": opts.get("preset") or "medium",
        "crf": opts.get("crf", 20),
        "pix_fmt": opts.get("pix_fmt") or "yuv420p",
        "abitrate": opts.get("abitrate") or "192k",
        "vbitrate": opts.get("vbitrate"),
    }
    graph = Graph(project, clips, opts)
    total = graph.total_duration()
    if total <= 1e-3:
        raise ValueError("时间轴为空，没有可导出的内容")

    out_dir = job_dir(jid)
    out_path = os.path.join(out_dir, (opts.get("filename") or "output") + ".mp4").replace("\\", "/")
    job = {
        "id": jid, "project_id": project.get("id"), "status": "queued",
        "progress": 0.0, "started": time.time(), "finished": None,
        "output": out_path, "duration": total, "warnings": [],
        "encoder": enc, "opts": opts, "cancel": False,
    }
    with _LOCK:
        _JOBS[jid] = job
        _ORDER.append(jid)
        while len(_ORDER) > _MAX_JOBS:
            _JOBS.pop(_ORDER.pop(0), None)
    threading.Thread(target=_run_job, args=(jid, graph, enc, out_path, total), daemon=True).start()
    return _public(_JOBS[jid])


def _run_job(jid: str, graph: Graph, enc: Dict[str, Any], out_path: str, total: float) -> None:
    try:
        cmd = graph.command(out_path, enc)
    except Exception as exc:
        _update(jid, status="failed", error="构建命令失败: " + str(exc))
        return
    _update(jid, status="running", warnings=list(graph.warnings))

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW, bufsize=0)
    except OSError as exc:
        _update(jid, status="failed", error="无法启动 ffmpeg: " + str(exc))
        return
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid]["proc"] = proc

    tail: List[str] = []
    last_emit = 0.0
    cancelled = False
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
        m = _TIME_RE.search(line)
        if m:
            done = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            p = min(0.999, done / total) if total > 0 else 0.0
            now = time.time()
            if now - last_emit > 0.4:
                last_emit = now
                started = (_JOBS.get(jid, {}) or {}).get("started") or now
                elapsed = max(0.001, now - started)
                spd = done / elapsed if elapsed > 0 else 0.0
                eta = (total - done) / spd if spd > 0.01 else None
                _update(jid, progress=p, speed=round(spd, 3),
                        eta=round(eta, 1) if eta is not None else None)
        with _LOCK:
            job = _JOBS.get(jid)
            if job and job.get("cancel"):
                cancelled = True
                try:
                    proc.kill()
                except OSError:
                    pass
                break

    rc = proc.wait()
    with _LOCK:
        job = _JOBS.get(jid)
        if job:
            job.pop("proc", None)

    if cancelled:
        _update(jid, status="cancelled", finished=time.time(), progress=0.0)
        _cleanup(out_path)
        return
    if rc != 0:
        _update(jid, status="failed", finished=time.time(),
                error=("\n".join(tail[-12:]) or ("ffmpeg 退出码 " + str(rc)))[-1200:])
        return
    try:
        size = os.path.getsize(out_path)
    except OSError:
        size = 0
    if size <= 0:
        _update(jid, status="failed", error="未生成输出文件", finished=time.time())
        return
    _update(jid, status="done", progress=1.0, finished=time.time(),
            size=size, speed=None, eta=0.0)


def _cleanup(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def delete_export(jid: str) -> bool:
    with _LOCK:
        job = _JOBS.get(jid)
        if not job:
            return False
        if job.get("status") in ("running", "queued"):
            raise ValueError("任务进行中，请先取消")
        out = job.get("output")
        _JOBS.pop(jid, None)
        if jid in _ORDER:
            _ORDER.remove(jid)
    if out:
        _cleanup(out)
        d = os.path.dirname(out)
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
    return True


PRESETS: Dict[str, Dict[str, Any]] = {
    "h264_high": {"label": "H.264 高质量", "vcodec": "libx264",
                  "preset": "slow", "crf": 16, "abitrate": "256k", "pix_fmt": "yuv420p"},
    "h264_standard": {"label": "H.264 标准（默认）", "vcodec": "libx264",
                      "preset": "medium", "crf": 20, "abitrate": "192k", "pix_fmt": "yuv420p"},
    "h264_web": {"label": "H.264 网络预览", "vcodec": "libx264",
                 "preset": "veryfast", "crf": 28, "abitrate": "128k", "pix_fmt": "yuv420p"},
    "h265_high": {"label": "H.265 高质量", "vcodec": "libx265",
                  "preset": "medium", "crf": 22, "abitrate": "192k", "pix_fmt": "yuv420p"},
}


# __EOF__