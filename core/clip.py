# -*- coding: utf-8 -*-
"""剪辑片段(Clip)对象模型。

一个 Clip = 媒体源引用 + 视频属性 + 音频属性 + 效果链 + 标记点。
所有可动参数均为 KeyframeTrack；所有对象可独立序列化为 JSON 复用。
"""
import copy
import time
import uuid
from typing import Any, Dict, List, Optional

from . import keyframe as kf


def S(v: Any) -> Dict[str, Any]:
    return {"static": v}


def default_video() -> Dict[str, Any]:
    return {
        "enabled": True,
        "src_in": S(0.0),
        "src_out": S(None),
        "speed": S(1.0),
        "scale": S(1.0),
        "pos_x": S(0.0),
        "pos_y": S(0.0),
        "rotation": S(0.0),
        "anchor_x": S(0.5),
        "anchor_y": S(0.5),
        "crop": {"top": S(0.0), "bottom": S(0.0), "left": S(0.0), "right": S(0.0)},
        "mirror_h": S(False),
        "mirror_v": S(False),
        "erode": S(0.0),
        "erode_softness": S(0.0),
        "erode_angle": S(0.0),   # 0~360 度，0=从左往右擦，90=从上往下
        "opacity": S(1.0),
        "blend": "normal",
    }


def default_audio() -> Dict[str, Any]:
    return {
        "enabled": True,
        "src_in": S(0.0),
        "src_out": S(None),
        "speed": S(1.0),
        "pitch": S(0.0),
        "gain": S(0.0),
        "fade_in": S(0.0),
        "fade_out": S(0.0),
        "pan": S(0.0),
    }


VIDEO_KF_FIELDS = [
    "src_in", "src_out", "speed", "scale", "pos_x", "pos_y", "rotation",
    "anchor_x", "anchor_y", "mirror_h", "mirror_v", "erode",
    "erode_softness", "opacity",
]
CROP_FIELDS = ["top", "bottom", "left", "right"]
AUDIO_KF_FIELDS = [
    "src_in", "src_out", "speed", "pitch", "gain", "fade_in", "fade_out", "pan",
]


def new_id(prefix: str = "clip") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def make_clip(media: Dict[str, Any], name: Optional[str] = None,
              track_id: Optional[str] = None, start: float = 0.0,
              duration: Optional[float] = None) -> Dict[str, Any]:
    now = time.time()
    kind = media.get("kind", "video")
    c = {
        "id": new_id("clip"),
        "name": name or media.get("name") or "未命名片段",
        "media": {
            "path": media.get("path"),
            "kind": kind,
            "probe": media.get("probe") or {},
        },
        "timeline": {
            "track_id": track_id,
            "start": float(start),
            "duration": duration,
            "z": 0,
        },
        "video": default_video(),
        "audio": default_audio(),
        "effects": [],
        "markers": [],
        "meta": {
            "created": now,
            "updated": now,
            "author": "ui",
            "tags": [],
            "notes": "",
        },
        "rev": 1,
    }
    if kind == "audio":
        c["video"]["enabled"] = False
    if kind == "image":
        c["audio"]["enabled"] = False
    if kind == "video":
        # 视频片段的音频一律禁用：声音必须走独立的音频片段，
        # 这样音量/音调/裁剪与画面互不影响。
        c["audio"]["enabled"] = False
    return c


def _norm_track(x: Any, default: Any) -> Dict[str, Any]:
    if kf.is_track(x):
        t = dict(x)
        if "keys" in t:
            t["keys"] = sorted(
                [dict(k) for k in t.get("keys") or []],
                key=lambda k: float(k.get("t", 0.0)),
            )
        return t
    return S(x if x is not None else default)


def normalize(clip: Dict[str, Any]) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    dv, da = default_video(), default_audio()

    c.setdefault("id", new_id())
    c.setdefault("name", "未命名片段")
    c.setdefault("media", {"path": None, "kind": "video", "probe": {}})
    c["media"].setdefault("kind", "video")
    c["media"].setdefault("probe", {})

    c.setdefault("timeline", {})
    c["timeline"].setdefault("track_id", None)
    c["timeline"].setdefault("start", 0.0)
    c["timeline"].setdefault("duration", None)
    c["timeline"].setdefault("z", 0)

    v = c.setdefault("video", {})
    for k, dvv in dv.items():
        if k == "crop":
            crop = v.setdefault("crop", {})
            for f in CROP_FIELDS:
                crop[f] = _norm_track(crop.get(f), dv["crop"][f]["static"])
        elif k in ("blend", "enabled", "erode_dir", "erode_angle"):
            # 普通字段（字符串/布尔），不是 keyframe track
            v.setdefault(k, dvv)
        else:
            v[k] = _norm_track(v.get(k), dvv["static"])

    a = c.setdefault("audio", {})
    for k, avv in da.items():
        if k == "enabled":
            a.setdefault("enabled", avv)
        else:
            a[k] = _norm_track(a.get(k), avv["static"])

    c.setdefault("effects", [])
    c.setdefault("markers", [])
    c.setdefault("meta", {})
    c["meta"].setdefault("created", time.time())
    c["meta"].setdefault("updated", time.time())
    c["meta"].setdefault("author", "ui")
    c["meta"].setdefault("tags", [])
    c["meta"].setdefault("notes", "")
    c.setdefault("rev", 1)
    return c


def _set_path(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            raise ValueError(f"路径 {path} 中途不是对象：{p}")
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _coerce_value(section: str, param: str, value: Any) -> Any:
    if isinstance(value, dict) and ("static" in value or "keys" in value):
        if "keys" in value:
            keys = []
            for k in value.get("keys") or []:
                e = k.get("e", "linear")
                if e not in kf.EASE_MODES:
                    e = "linear"
                nk = {"t": float(k.get("t", 0.0)), "v": k.get("v"), "e": e}
                if e == "bezier":
                    nk["h"] = list(k.get("h") or kf.DEFAULT_BEZIER)
                keys.append(nk)
            keys.sort(key=lambda x: x["t"])
            return {"keys": keys}
        return {"static": value.get("static")}
    return {"static": value}


def apply_patch(clip: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    for path, value in patch.items():
        if path in ("id", "rev", "meta.created"):
            continue
        parts = path.split(".")
        head = parts[0]
        if head == "video" and len(parts) >= 2:
            if parts[1] == "crop" and len(parts) == 3:
                if parts[2] in CROP_FIELDS:
                    value = _coerce_value("crop", parts[2], value)
            elif parts[1] in VIDEO_KF_FIELDS:
                value = _coerce_value("video", parts[1], value)
            _set_path(c, path, value)
        elif head == "audio" and len(parts) >= 2:
            if parts[1] in AUDIO_KF_FIELDS:
                value = _coerce_value("audio", parts[1], value)
            _set_path(c, path, value)
        elif head in ("timeline", "media", "meta", "name", "effects", "markers"):
            _set_path(c, path, value)
        else:
            _set_path(c, path, value)
    c["meta"]["updated"] = time.time()
    return c


def resolve_at(clip: Dict[str, Any], t_local: float) -> Dict[str, Any]:
    v = clip.get("video", {})
    a = clip.get("audio", {})
    out = {
        "t": t_local,
        "video": {
            "enabled": v.get("enabled", True),
            "opacity": kf.eval_track(v.get("opacity"), t_local, 1.0),
            "scale": kf.eval_track(v.get("scale"), t_local, 1.0),
            "pos": [
                kf.eval_track(v.get("pos_x"), t_local, 0.0),
                kf.eval_track(v.get("pos_y"), t_local, 0.0),
            ],
            "rotation": kf.eval_track(v.get("rotation"), t_local, 0.0),
            "anchor": [
                kf.eval_track(v.get("anchor_x"), t_local, 0.5),
                kf.eval_track(v.get("anchor_y"), t_local, 0.5),
            ],
            "speed": kf.eval_track(v.get("speed"), t_local, 1.0),
            "crop": {f: kf.eval_track((v.get("crop") or {}).get(f), t_local, 0.0)
                     for f in CROP_FIELDS},
            "mirror": [
                bool(kf.eval_track(v.get("mirror_h"), t_local, False)),
                bool(kf.eval_track(v.get("mirror_v"), t_local, False)),
            ],
            "erode": kf.eval_track(v.get("erode"), t_local, 0.0),
            "erode_softness": kf.eval_track(v.get("erode_softness"), t_local, 0.0),
            "erode_angle": kf.eval_track(v.get("erode_angle"), t_local, 0.0),
            "blend": v.get("blend", "normal"),
        },
        "audio": {
            "enabled": a.get("enabled", True),
            "gain": kf.eval_track(a.get("gain"), t_local, 0.0),
            "pitch": kf.eval_track(a.get("pitch"), t_local, 0.0),
            "pan": kf.eval_track(a.get("pan"), t_local, 0.0),
            "speed": kf.eval_track(a.get("speed"), t_local, 1.0),
        },
        "effects": [],
    }
    for eff in clip.get("effects", []):
        if not eff.get("enabled", True):
            continue
        params = {}
        for k, tr in (eff.get("params") or {}).items():
            params[k] = kf.eval_track(tr, t_local, None)
        out["effects"].append({"id": eff.get("id"), "type": eff.get("type"), "params": params})
    return out


def source_time_at(clip: Dict[str, Any], t_local: float) -> float:
    v = clip["video"] if clip["media"].get("kind") != "audio" else clip["audio"]
    s_in = kf.eval_track(v.get("src_in"), t_local, 0.0) or 0.0
    spd = kf.eval_track(v.get("speed"), t_local, 1.0) or 1.0
    return s_in + t_local * spd


def duration_of(clip: Dict[str, Any]) -> Optional[float]:
    tl = clip.get("timeline", {})
    if tl.get("duration") is not None:
        return float(tl["duration"])
    sec = clip["video"] if clip["media"].get("kind") != "audio" else clip["audio"]
    probe = clip.get("media", {}).get("probe") or {}
    src_dur = probe.get("duration")
    s_in = kf.eval_track(sec.get("src_in"), 0.0, 0.0) or 0.0
    s_out = kf.eval_track(sec.get("src_out"), 0.0, None)
    if s_out is None:
        if src_dur is None:
            return None
        s_out = float(src_dur)
    spd = kf.eval_track(sec.get("speed"), 0.0, 1.0) or 1.0
    if spd <= 0:
        spd = 1.0
    return max(0.0, (float(s_out) - float(s_in)) / spd)


def source_duration(clip: Dict[str, Any]) -> Optional[float]:
    """只按源区间与倍速算时长，忽略 timeline.duration 缓存。

    改 speed/src_in/src_out 后必须用这个重算，否则时间轴长度不跟着变。
    """
    sec = "audio" if (clip.get("media") or {}).get("kind") == "audio" else "video"
    node = clip.get(sec) or {}
    probe = (clip.get("media") or {}).get("probe") or {}
    src_dur = probe.get("duration")
    s_in = kf.eval_track(node.get("src_in"), 0.0, 0.0) or 0.0
    s_out = kf.eval_track(node.get("src_out"), 0.0, None)
    if s_out is None:
        if src_dur is None:
            return None
        s_out = float(src_dur)
    spd = kf.eval_track(node.get("speed"), 0.0, 1.0) or 1.0
    if spd <= 0:
        spd = 1.0
    return max(0.0, (float(s_out) - float(s_in)) / spd)


def add_keyframe(clip: Dict[str, Any], path: str, t: float, v: Any,
                 e: str = "linear", h: Optional[List[float]] = None) -> Dict[str, Any]:
    """path 支持 video.opacity / video.crop.left / audio.gain / effects.<id>.<param>"""
    c = copy.deepcopy(clip)
    if path.startswith("effects."):
        _, eid, param = path.split(".", 2)
        for eff in c.get("effects", []):
            if eff.get("id") == eid or eff.get("type") == eid:
                eff.setdefault("params", {})
                eff["params"][param] = kf.set_key(eff["params"].get(param), t, v, e, h)
                c["meta"]["updated"] = time.time()
                return c
        raise KeyError(f"效果不存在: {eid}")
    parts = path.split(".")
    node: Any = c
    for p in parts[:-1]:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            raise KeyError(f"路径不存在: {path}")
    leaf = parts[-1]
    node[leaf] = kf.set_key(node.get(leaf), t, v, e, h)
    c["meta"]["updated"] = time.time()
    return c


def remove_keyframe(clip: Dict[str, Any], path: str, t: float) -> Dict[str, Any]:
    c = copy.deepcopy(clip)
    if path.startswith("effects."):
        _, eid, param = path.split(".", 2)
        for eff in c.get("effects", []):
            if eff.get("id") == eid or eff.get("type") == eid:
                eff["params"][param] = kf.remove_key(eff["params"].get(param), t)
                c["meta"]["updated"] = time.time()
                return c
        raise KeyError(f"效果不存在: {eid}")
    parts = path.split(".")
    node: Any = c
    for p in parts[:-1]:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            raise KeyError(f"路径不存在: {path}")
    leaf = parts[-1]
    node[leaf] = kf.remove_key(node.get(leaf), t)
    c["meta"]["updated"] = time.time()
    return c


# __EOF__