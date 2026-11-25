# -*- coding: utf-8 -*-
"""素材箱：登记源文件，派生可上时间轴的卡片。

设计要点：
  - 素材只登记**引用**，不复制文件
  - 一个视频源（含音轨）派生出两张卡：role=video / role=audio
  - 卡片拖到时间轴时才生成 clip，素材本身不占轨道
  - role=audio 的 clip 用 media.kind="audio" + 源文件路径，
    这样 duration/导出都走音频段，预览自动跳过视频
"""
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import media as media_mod

_LOCK = threading.RLock()


def assets_path(pid: str) -> str:
    p = os.path.join(cfg.ROOT, "data", "projects", str(pid), "assets.json").replace("\\", "/")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _read(pid: str) -> List[Dict[str, Any]]:
    p = assets_path(pid)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(pid: str, items: List[Dict[str, Any]]) -> None:
    p = assets_path(pid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ---------------------------------------------------------------- 卡片派生

def expand(asset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把一个源登记展开成若干卡片（video / audio / image）。"""
    probe = asset.get("probe") or {}
    kind = asset.get("kind") or "video"
    path = asset.get("path")
    dur = probe.get("duration")
    name = asset.get("name") or os.path.basename(path or "")

    base = {
        "asset_id": asset.get("id"),
        "path": path,
        "name": name,
        "duration": dur,
        "width": probe.get("width") or 0,
        "height": probe.get("height") or 0,
        "fps": probe.get("fps") or 0,
        "has_video": bool(probe.get("has_video")),
        "has_audio": bool(probe.get("has_audio")),
        "browser_ok": bool(probe.get("browser_ok", True)),
        "browser_issues": probe.get("browser_issues") or [],
        "thumb": "/api/media/thumbnail?path=%s&t=1&w=320" % _q(path),
        "created": asset.get("created"),
    }

    cards: List[Dict[str, Any]] = []

    if kind == "image":
        cards.append({**base, "role": "image", "kind": "image",
                      "label": name, "suffix": "图片"})
        return cards

    if kind == "audio":
        cards.append({**base, "role": "audio", "kind": "audio",
                      "label": _stem(name), "suffix": "音频"})
        return cards

    # 视频源
    if probe.get("has_video"):
        cards.append({**base, "role": "video", "kind": "video",
                      "label": _stem(name), "suffix": "视频"})
    if probe.get("has_audio"):
        cards.append({**base, "role": "audio", "kind": "audio",
                      "label": _stem(name) + " · 音频", "suffix": "音频"})

    if not cards:  # 探测不出任何流，仍给一张视频卡兜底
        cards.append({**base, "role": "video", "kind": "video",
                      "label": _stem(name), "suffix": "视频"})
    return cards


def _stem(name: str) -> str:
    return os.path.splitext(name)[0] or name


def _q(s: Optional[str]) -> str:
    from urllib.parse import quote
    return quote(str(s or ""), safe="")


# ---------------------------------------------------------------- 增删查

def list_assets(pid: str) -> Dict[str, Any]:
    """返回 {assets:[源登记], cards:[展开卡片]}。"""
    items = _read(pid)
    cards: List[Dict[str, Any]] = []
    for a in items:
        cards.extend(expand(a))
    return {"assets": items, "cards": cards, "count": len(items)}


def find(pid: str, asset_id: str) -> Optional[Dict[str, Any]]:
    for a in _read(pid):
        if a.get("id") == asset_id:
            return a
    return None


def add(pid: str, path: str, name: Optional[str] = None) -> Dict[str, Any]:
    """登记一个源文件。同一路径已存在则直接返回（幂等）。"""
    path = str(path).replace("\\", "/")
    with _LOCK:
        items = _read(pid)
        for a in items:
            if a.get("path") == path:
                return {"asset": a, "cards": expand(a), "existed": True}

        info = media_mod.probe(path)
        if not info.get("ok"):
            raise ValueError("媒体探测失败: " + str(info.get("error") or path))

        asset = {
            "id": "asset_" + uuid.uuid4().hex[:10],
            "path": path,
            "name": name or info.get("name") or os.path.basename(path),
            "kind": info.get("kind") or "video",
            "probe": info,
            "created": time.time(),
        }
        items.append(asset)
        _write(pid, items)
        return {"asset": asset, "cards": expand(asset), "existed": False}


def remove(pid: str, asset_id: str) -> bool:
    with _LOCK:
        items = _read(pid)
        keep = [a for a in items if a.get("id") != asset_id]
        if len(keep) == len(items):
            return False
        _write(pid, keep)
        return True


def clear(pid: str) -> None:
    with _LOCK:
        _write(pid, [])


def refresh(pid: str, asset_id: Optional[str] = None) -> int:
    """重新探测（源文件被替换/编码变了时用）。返回更新条数。"""
    with _LOCK:
        items = _read(pid)
        n = 0
        for a in items:
            if asset_id and a.get("id") != asset_id:
                continue
            info = media_mod.probe(a["path"], use_cache=False)
            if info.get("ok"):
                a["probe"] = info
                a["kind"] = info.get("kind") or a.get("kind")
                n += 1
        if n:
            _write(pid, items)
        return n


# __EOF__