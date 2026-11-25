# -*- coding: utf-8 -*-
"""项目(时间轴)与轨道模型。"""
import copy
import time
import uuid
from typing import Any, Dict, List, Optional

TRACK_TYPES = ("video", "audio", "effect", "marker", "subtitle")

DEFAULT_SETTINGS = {
    "width": 1920,
    "height": 1080,
    "fps": 60,
    "sample_rate": 48000,
    "channels": 2,
    "background": "#000000",
}


def new_id(prefix: str = "proj") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def make_track(ttype: str, name: Optional[str] = None, index: int = 0) -> Dict[str, Any]:
    return {
        "id": f"trk_{ttype[0]}{uuid.uuid4().hex[:6]}",
        "type": ttype,
        "name": name or ttype.upper(),
        "index": index,
        "clips": [],
        "locked": False,
        "muted": False,
        "hidden": False,
        "height": 64,
    }


def make_project(name: str = "未命名项目", settings: Optional[Dict[str, Any]] = None,
                 author: str = "ui") -> Dict[str, Any]:
    now = time.time()
    st = dict(DEFAULT_SETTINGS)
    st.update(settings or {})
    tracks = [
        make_track("video", "V2", 0),
        make_track("video", "V1", 1),
        make_track("subtitle", "SUB1", 2),
        make_track("audio", "A1", 3),
    ]
    return {
        "id": new_id("proj"),
        "name": name,
        "settings": st,
        "tracks": tracks,
        "markers": [],
        "subtitles": [],
        "playhead": 0.0,
        "shared_playhead": False,
        "meta": {
            "created": now,
            "updated": now,
            "author": author,
            "description": "",
        },
        "rev": 1,
    }


def normalize(project: Dict[str, Any]) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    st = dict(DEFAULT_SETTINGS)
    st.update(p.get("settings") or {})
    p["settings"] = st
    p.setdefault("tracks", [])
    p.setdefault("markers", [])
    p.setdefault("subtitles", [])
    p.setdefault("playhead", 0.0)
    p.setdefault("shared_playhead", False)
    p.setdefault("meta", {})
    p["meta"].setdefault("created", time.time())
    p["meta"].setdefault("updated", time.time())
    p["meta"].setdefault("author", "ui")
    p["meta"].setdefault("description", "")
    p.setdefault("rev", 1)
    p.setdefault("name", "未命名项目")
    for i, tr in enumerate(p["tracks"]):
        tr.setdefault("id", f"trk_{uuid.uuid4().hex[:8]}")
        tr.setdefault("type", "video")
        tr.setdefault("name", str(tr["type"]).upper())
        tr.setdefault("index", i)
        tr.setdefault("clips", [])
        tr.setdefault("locked", False)
        tr.setdefault("muted", False)
        tr.setdefault("hidden", False)
        tr.setdefault("height", 64)
    p["tracks"].sort(key=lambda t: t.get("index", 0))
    return p


def find_track(project: Dict[str, Any], track_id: str) -> Optional[Dict[str, Any]]:
    for tr in project.get("tracks", []):
        if tr["id"] == track_id:
            return tr
    return None


def add_track(project: Dict[str, Any], ttype: str, name: Optional[str] = None,
              index: Optional[int] = None) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    if ttype not in TRACK_TYPES:
        raise ValueError(f"非法轨道类型: {ttype}")
    idx = len(p["tracks"]) if index is None else int(index)
    tr = make_track(ttype, name, idx)
    p["tracks"].append(tr)
    p["tracks"].sort(key=lambda t: t.get("index", 0))
    for i, t in enumerate(p["tracks"]):
        t["index"] = i
    p["meta"]["updated"] = time.time()
    return p


def remove_track(project: Dict[str, Any], track_id: str) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p["tracks"] = [t for t in p["tracks"] if t["id"] != track_id]
    for i, t in enumerate(p["tracks"]):
        t["index"] = i
    p["meta"]["updated"] = time.time()
    return p


def reorder_tracks(project: Dict[str, Any], order: List[str]) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    by_id = {t["id"]: t for t in p["tracks"]}
    new_list = [by_id[i] for i in order if i in by_id]
    for t in p["tracks"]:
        if t["id"] not in order:
            new_list.append(t)
    for i, t in enumerate(new_list):
        t["index"] = i
    p["tracks"] = new_list
    p["meta"]["updated"] = time.time()
    return p


def attach_clip(project: Dict[str, Any], track_id: str, clip_id: str,
                at: Optional[int] = None) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    tr = find_track(p, track_id)
    if tr is None:
        raise KeyError(f"轨道不存在: {track_id}")
    if clip_id in tr["clips"]:
        return p
    if at is None:
        tr["clips"].append(clip_id)
    else:
        tr["clips"].insert(int(at), clip_id)
    p["meta"]["updated"] = time.time()
    return p


def detach_clip(project: Dict[str, Any], clip_id: str) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    for tr in p["tracks"]:
        if clip_id in tr["clips"]:
            tr["clips"].remove(clip_id)
    p["meta"]["updated"] = time.time()
    return p


def move_clip(project: Dict[str, Any], clip_id: str, to_track: str) -> Dict[str, Any]:
    p = detach_clip(project, clip_id)
    return attach_clip(p, to_track, clip_id)


def add_marker(project: Dict[str, Any], t: float, label: str = "",
               color: str = "#ffcc00", track_id: Optional[str] = None) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p["markers"].append({
        "id": f"mk_{uuid.uuid4().hex[:8]}",
        "t": float(t),
        "label": label,
        "color": color,
        "track_id": track_id,
        "created": time.time(),
    })
    p["markers"].sort(key=lambda m: m["t"])
    p["meta"]["updated"] = time.time()
    return p


def remove_marker(project: Dict[str, Any], marker_id: str) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p["markers"] = [m for m in p["markers"] if m["id"] != marker_id]
    p["meta"]["updated"] = time.time()
    return p


# ---------------------------------------------------------------- 字幕

SUB_STYLE_DEFAULT = {
    "fontSize": 48,
    "color": "#ffffff",
    "outlineColor": "#000000",
    "outline": 3,
    "x": 0.5,
    "y": 0.86,
    "align": "center",
    "bold": True,
}


def _norm_sub_style(style: Any) -> Dict[str, Any]:
    out = dict(SUB_STYLE_DEFAULT)
    if isinstance(style, dict):
        for k, v in style.items():
            if k in out:
                out[k] = v
    try:
        out["fontSize"] = max(8, min(400, int(out["fontSize"])))
    except (TypeError, ValueError):
        out["fontSize"] = 48
    try:
        out["x"] = max(0.0, min(1.0, float(out["x"])))
        out["y"] = max(0.0, min(1.0, float(out["y"])))
    except (TypeError, ValueError):
        out["x"], out["y"] = 0.5, 0.86
    if out["align"] not in ("left", "center", "right"):
        out["align"] = "center"
    return out


def add_subtitle(project: Dict[str, Any], start: float, end: float, text: str,
                 style: Any = None) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p.setdefault("subtitles", [])
    st = max(0.0, float(start))
    en = max(st + 0.05, float(end))
    p["subtitles"].append({
        "id": f"sub_{uuid.uuid4().hex[:10]}",
        "start": st,
        "end": en,
        "text": str(text or ""),
        "style": _norm_sub_style(style),
    })
    p["subtitles"].sort(key=lambda x: x["start"])
    p["meta"]["updated"] = time.time()
    return p


def update_subtitle(project: Dict[str, Any], sid: str,
                    patch: Dict[str, Any]) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    for item in p.get("subtitles") or []:
        if item.get("id") != sid:
            continue
        if "start" in patch:
            item["start"] = max(0.0, float(patch["start"]))
        if "end" in patch:
            item["end"] = max(item["start"] + 0.05, float(patch["end"]))
        if "text" in patch:
            item["text"] = str(patch["text"] or "")
        if "style" in patch and isinstance(patch["style"], dict):
            merged = dict(item.get("style") or {})
            merged.update(patch["style"])
            item["style"] = _norm_sub_style(merged)
        break
    p["subtitles"].sort(key=lambda x: x["start"])
    p["meta"]["updated"] = time.time()
    return p


def remove_subtitle(project: Dict[str, Any], sid: str) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p["subtitles"] = [x for x in (p.get("subtitles") or [])
                      if x.get("id") != sid]
    p["meta"]["updated"] = time.time()
    return p


def find_subtitle(project: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    for x in project.get("subtitles") or []:
        if x.get("id") == sid:
            return x
    return None


def total_duration(project: Dict[str, Any], durations: Dict[str, float]) -> float:
    """durations: clip_id -> 秒。返回时间轴总长度。"""
    end = 0.0
    for tr in project["tracks"]:
        for cid in tr["clips"]:
            d = durations.get(cid)
            if d:
                end = max(end, d)
    return end


# __EOF__