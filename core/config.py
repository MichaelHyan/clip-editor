# -*- coding: utf-8 -*-
"""配置加载与路径安全。"""
import json
import os
import threading
from typing import Any, Dict, List

_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5173,
    "debug": False,
    "media_roots": ["D:/", "C:/Users"],
    "media_trust_all": True,
    "cache_dir": "data/cache",
    "history_limit": 200,
    "snapshot_interval_sec": 300,
    "proxy": {
        "enabled": True,
        "max_height": 720,
        "crf": 28,
        "preset": "veryfast",
        "threshold_mb": 40,
    },
    "default_project": {
        "width": 1920,
        "height": 1080,
        "fps": 60,
        "sample_rate": 48000,
        "channels": 2,
        "background": "#000000",
    },
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(force: bool = False) -> Dict[str, Any]:
    with _LOCK:
        if _CACHE.get("cfg") is not None and not force:
            return _CACHE["cfg"]
        cfg = dict(_DEFAULTS)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = _merge(cfg, json.load(f))
        except (OSError, ValueError):
            pass
        _CACHE["cfg"] = cfg
        return cfg


def save(cfg: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        merged = _merge(_DEFAULTS, cfg)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        _CACHE["cfg"] = merged
        return merged


def get(key: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def abs_path(rel: str) -> str:
    p = str(rel).replace("\\", "/")
    if os.path.isabs(p):
        return os.path.normpath(p).replace("\\", "/")
    return os.path.normpath(os.path.join(ROOT, p)).replace("\\", "/")


def data_dir(*parts: str) -> str:
    p = os.path.join(ROOT, "data", *parts)
    if os.path.splitext(p)[1]:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    else:
        os.makedirs(p, exist_ok=True)
    return p.replace("\\", "/")


def cache_dir(*parts: str) -> str:
    base = abs_path(get("cache_dir", "data/cache"))
    p = os.path.join(base, *parts)
    os.makedirs(p, exist_ok=True)
    return p.replace("\\", "/")


def media_roots() -> List[str]:
    """规范化根目录列表。盘符根保留为 D:/，不被 rstrip 成 D:（后者 realpath 会解析成 cwd）。"""
    out: List[str] = []
    for r in (get("media_roots") or []):
        s = os.path.normpath(str(r).replace("\\", "/")).replace("\\", "/")
        out.append(s)
    return out


def is_media_allowed(path: str) -> bool:
    if not path:
        return False
    try:
        p = os.path.normpath(str(path).replace("\\", "/")).replace("\\", "/")
    except (OSError, ValueError):
        return False
    if not os.path.isabs(p):
        return False
    if bool(get("media_trust_all", True)):
        return True
    real = os.path.realpath(p).replace("\\", "/").lower()
    for r in media_roots():
        rr = os.path.realpath(r).replace("\\", "/").lower()
        if not rr.endswith("/"):
            rr = rr + "/"
        if real == rr.rstrip("/") or real.startswith(rr):
            return True
    return False


# __EOF__