# -*- coding: utf-8 -*-
"""片段编辑操作：复制、切分、修剪、关键帧整体平移。"""
import copy
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import keyframe as kf

_MOVABLE_SECTIONS = ("video", "audio")


def _new_clip_id() -> str:
    return "clip_" + uuid.uuid4().hex[:12]


def _shift_keys(track: Any, dt: float, drop_before: bool = True,
                drop_after: Optional[float] = None) -> Any:
    if not kf.has_keys(track):
        return copy.deepcopy(track)
    out: List[Dict[str, Any]] = []
    for k in track.get("keys") or []:
        t = float(k.get("t", 0.0)) + dt
        if drop_before and t < -1e-6:
            continue
        if drop_after is not None and t > drop_after + 1e-6:
            continue
        nk = copy.deepcopy(k)
        nk["t"] = round(max(0.0, t), 6)
        out.append(nk)
    if not out:
        v = kf.eval_track(track, max(0.0, -dt), None)
        return {"static": v}
    out.sort(key=lambda x: x["t"])
    if len(out) == 1:
        return {"static": out[0].get("v")}
    return {"keys": out}


def _trim_tracks(clip: Dict[str, Any], t_from: float,
                 t_to: Optional[float]) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    span = None if t_to is None else max(0.0, t_to - t_from)
    for sec in _MOVABLE_SECTIONS:
        node = c.get(sec)
        if not isinstance(node, dict):
            continue
        for k, v in list(node.items()):
            if k == "crop" and isinstance(v, dict):
                for f, tr in list(v.items()):
                    v[f] = _shift_keys(tr, -t_from, True, span)
            elif kf.is_track(v) or isinstance(v, (int, float, bool)):
                node[k] = _shift_keys(v, -t_from, True, span)
    for eff in c.get("effects", []):
        params = eff.get("params") or {}
        for k, tr in list(params.items()):
            params[k] = _shift_keys(tr, -t_from, True, span)
    if span is not None:
        c.setdefault("markers", [])
        c["markers"] = [
            {**m, "t": round(float(m.get("t", 0.0)) - t_from, 6)}
            for m in c["markers"]
            if t_from - 1e-6 <= float(m.get("t", 0.0)) <= (t_to + 1e-6)
        ]
    return c


def _duration(clip: Dict[str, Any]) -> float:
    tl = clip.get("timeline") or {}
    if tl.get("duration") is not None:
        return float(tl["duration"])
    sec = "audio" if (clip.get("media") or {}).get("kind") == "audio" else "video"
    node = clip.get(sec) or {}
    probe = (clip.get("media") or {}).get("probe") or {}
    s_in = kf.eval_track(node.get("src_in"), 0.0, 0.0) or 0.0
    s_out = kf.eval_track(node.get("src_out"), 0.0, None)
    if s_out is None:
        s_out = probe.get("duration")
    spd = kf.eval_track(node.get("speed"), 0.0, 1.0) or 1.0
    if not s_out or spd <= 0:
        return 0.0
    return max(0.0, (float(s_out) - float(s_in)) / float(spd))


def duplicate(clip: Dict[str, Any], name: Optional[str] = None) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    c["id"] = _new_clip_id()
    c["name"] = name or (str(clip.get("name") or "片段") + " 副本")
    c["meta"] = dict(c.get("meta") or {})
    c["meta"]["created"] = time.time()
    c["meta"]["updated"] = time.time()
    c["rev"] = 1
    for eff in c.get("effects", []):
        eff["id"] = "fx_" + uuid.uuid4().hex[:8]
    return c


def _shift_src_in(clip: Dict[str, Any], dt: float) -> None:
    sec = "audio" if (clip.get("media") or {}).get("kind") == "audio" else "video"
    node = clip.get(sec) or {}
    spd = kf.eval_track(node.get("speed"), 0.0, 1.0) or 1.0
    s_in = kf.eval_track(node.get("src_in"), 0.0, 0.0) or 0.0
    node["src_in"] = {"static": round(float(s_in) + dt * float(spd), 6)}
    if clip.get(sec) is not node:
        clip[sec] = node


def split(clip: Dict[str, Any], t_local: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """在 clip 内 t_local 秒处切成 A/B。B 的时间轴起点后移 t_local。"""
    dur = _duration(clip)
    t = max(0.0, min(float(t_local), dur))
    if t <= 1e-6 or t >= dur - 1e-6:
        raise ValueError(f"切分点 {t:.3f}s 超出有效范围 (0, {dur:.3f})")

    a = _trim_tracks(clip, 0.0, t)
    a["id"] = _new_clip_id()
    a["name"] = str(clip.get("name") or "片段") + " (1)"
    a["meta"] = dict(a.get("meta") or {})
    a["rev"] = 1
    for eff in a.get("effects", []):
        eff["id"] = "fx_" + uuid.uuid4().hex[:8]
    a["timeline"]["track_id"] = (clip.get("timeline") or {}).get("track_id")
    a["timeline"]["start"] = float((clip.get("timeline") or {}).get("start") or 0.0)
    a["timeline"]["duration"] = round(t, 6)

    b = _trim_tracks(clip, t, None)
    b["id"] = _new_clip_id()
    b["name"] = str(clip.get("name") or "片段") + " (2)"
    b["meta"] = dict(b.get("meta") or {})
    b["meta"]["created"] = time.time()
    b["meta"]["updated"] = time.time()
    b["rev"] = 1
    for eff in b.get("effects", []):
        eff["id"] = "fx_" + uuid.uuid4().hex[:8]
    b["timeline"]["start"] = round(float(clip["timeline"].get("start") or 0.0) + t, 6)
    b["timeline"]["duration"] = round(max(0.0, dur - t), 6)
    _shift_src_in(b, t)

    a["meta"]["updated"] = time.time()
    a["rev"] = int(a.get("rev", 1)) + 1
    return a, b


def trim(clip: Dict[str, Any], src_in: Optional[float] = None,
         src_out: Optional[float] = None) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    sec = "audio" if (c.get("media") or {}).get("kind") == "audio" else "video"
    node = c.setdefault(sec, {})
    if src_in is not None:
        node["src_in"] = {"static": float(src_in)}
    if src_out is not None:
        node["src_out"] = {"static": float(src_out)}
    c["timeline"]["duration"] = None
    c["meta"]["updated"] = time.time()
    c["rev"] = int(c.get("rev", 1)) + 1
    return c


# __EOF__