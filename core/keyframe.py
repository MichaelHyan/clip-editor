# -*- coding: utf-8 -*-
"""关键帧求值器。

【重要契约】本文件语义必须与前端 static/js/keyframe.js 逐行等价。
前端预览、后端导出（ffmpeg 表达式）共用同一套插值规则，保证所见即所得。

轨道(track)三种形态：
    {"static": 1.0}                                  静态值
    {"keys": [{"t":0,"v":1.0,"e":"linear"}, ...]}     关键帧序列
    1.0                                              裸值（自动视为 static）

关键帧字段：
    t : 时间（秒，相对 clip 起点，非时间轴绝对时间）
    v : 值
    e : 该键到下一个键之间的缓动，linear|hold|ease_in|ease_out|ease_in_out|bezier
    h : bezier 控制点 [x1,y1,x2,y2]，缺省 [0.4,0,0.6,1]
"""
from typing import Any, Dict, List, Optional

EASE_MODES = ("linear", "hold", "ease_in", "ease_out", "ease_in_out", "bezier")
DEFAULT_BEZIER = [0.4, 0.0, 0.6, 1.0]


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _bz(t: float, p1: float, p2: float) -> float:
    mt = 1.0 - t
    return 3.0 * mt * mt * t * p1 + 3.0 * mt * t * t * p2 + t * t * t


def _bz_solve(x: float, x1: float, x2: float) -> float:
    """解三次贝塞尔 x(t)=x，二分 24 次，误差 < 1e-7。"""
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) * 0.5
        if _bz(mid, x1, x2) < x:
            lo = mid
        else:
            hi = mid
    return (lo + hi) * 0.5


def ease(mode: str, u: float, h: Optional[List[float]] = None) -> float:
    u = _clamp01(u)
    if mode == "hold":
        return 0.0
    if mode == "ease_in":
        return u * u * u
    if mode == "ease_out":
        return 1.0 - (1.0 - u) ** 3
    if mode == "ease_in_out":
        return 4.0 * u ** 3 if u < 0.5 else 1.0 - ((-2.0 * u + 2.0) ** 3) / 2.0
    if mode == "bezier":
        hh = h if (isinstance(h, (list, tuple)) and len(h) >= 4) else DEFAULT_BEZIER
        x1, y1, x2, y2 = float(hh[0]), float(hh[1]), float(hh[2]), float(hh[3])
        if x1 <= 0.0 and x2 <= 0.0:
            return u
        return _bz(_bz_solve(u, x1, x2), y1, y2)
    return u


def is_track(x: Any) -> bool:
    return isinstance(x, dict) and ("static" in x or "keys" in x)


def has_keys(x: Any) -> bool:
    return isinstance(x, dict) and bool(x.get("keys"))


def as_track(x: Any) -> Dict[str, Any]:
    if is_track(x):
        return x
    return {"static": x}


def eval_track(track: Any, t: float, default: Any = None) -> Any:
    """求 t 时刻（相对 clip 起点）的值。"""
    if track is None:
        return default
    if not is_track(track):
        return track
    if "static" in track:
        v = track["static"]
        return default if v is None else v
    keys = track.get("keys") or []
    if not keys:
        return default
    if not isinstance(t, (int, float)) or t != t:  # NaN
        t = 0.0
    if len(keys) == 1:
        return keys[0].get("v", default)
    keys = sorted(keys, key=lambda k: float(k.get("t", 0.0)))
    if t <= float(keys[0]["t"]):
        return keys[0].get("v", default)
    if t >= float(keys[-1]["t"]):
        return keys[-1].get("v", default)
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if float(keys[mid]["t"]) <= t:
            lo = mid
        else:
            hi = mid
    k0, k1 = keys[lo], keys[hi]
    span = float(k1["t"]) - float(k0["t"])
    if span <= 0.0:
        return k1.get("v", default)
    u = (t - float(k0["t"])) / span
    f = ease(k0.get("e", "linear"), u, k0.get("h"))
    v0 = k0.get("v")
    v1 = k1.get("v")
    if isinstance(v0, bool) or isinstance(v1, bool):
        return bool(v0) if u < 1.0 else bool(v1)
    if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
        return v0 + (v1 - v0) * f
    return v0


def set_key(track: Any, t: float, v: Any, e: str = "linear",
            h: Optional[List[float]] = None) -> Dict[str, Any]:
    """在 track 上写入/覆盖一个关键帧，返回新 track（不修改入参）。"""
    if not has_keys(track):
        base = track.get("static") if is_track(track) else (track if track is not None else v)
        keys: List[Dict[str, Any]] = []
        if t > 1e-6 and base is not None and base != v:
            keys.append({"t": 0.0, "v": base, "e": "linear"})
    else:
        keys = [dict(k) for k in track["keys"]]
    e = e if e in EASE_MODES else "linear"
    kf = {"t": round(float(t), 6), "v": v, "e": e}
    if e == "bezier":
        kf["h"] = list(h) if h else list(DEFAULT_BEZIER)
    for i, k in enumerate(keys):
        if abs(float(k.get("t", 0.0)) - float(t)) < 1e-6:
            keys[i] = kf
            break
    else:
        keys.append(kf)
    keys.sort(key=lambda k: float(k.get("t", 0.0)))
    return {"keys": keys}


def remove_key(track: Any, t: float) -> Dict[str, Any]:
    if not has_keys(track):
        return as_track(track)
    keys = [dict(k) for k in track["keys"] if abs(float(k.get("t", 0.0)) - float(t)) > 1e-6]
    if not keys:
        return {"static": None}
    if len(keys) == 1:
        return {"static": keys[0].get("v")}
    return {"keys": keys}


def all_times(track: Any) -> List[float]:
    if not has_keys(track):
        return []
    return sorted(float(k.get("t", 0.0)) for k in track["keys"])


def bake(track: Any, t0: float, t1: float, fps: float = 60.0) -> List[List[float]]:
    """按帧采样，供导出端拟合分段线性表达式 / 前端预览校验使用。"""
    if not has_keys(track):
        return [[t0, eval_track(track, t0)]]
    step = 1.0 / max(fps, 1.0)
    n = max(1, int(round((t1 - t0) * max(fps, 1.0))))
    return [[round(t0 + i * step, 6), eval_track(track, t0 + i * step)] for i in range(n + 1)]


# __EOF__