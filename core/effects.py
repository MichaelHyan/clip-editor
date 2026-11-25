# -*- coding: utf-8 -*-
"""效果注册表。每个效果同时声明 params / preview / ffmpeg 三种表示。"""
import copy
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import config as cfg

_LOCK = threading.RLock()
_CHANGE_HANDLERS: List[Callable[[Dict[str, Any]], None]] = []
_REG: Dict[str, Any] = {"version": 1, "effects": {}}
_LOADED = False

PARAM_TYPES = ("number", "int", "bool", "select", "color", "text", "vec2")


def _p(t: str, default: Any, **kw: Any) -> Dict[str, Any]:
    d = {"type": t, "default": default}
    d.update(kw)
    return d


BUILTIN: Dict[str, Dict[str, Any]] = {
    "blur": {
        "name": "高斯模糊", "category": "filter", "scope": "video", "icon": "droplet",
        "params": {"radius": _p("number", 0.0, min=0.0, max=200.0, step=0.5,
                                unit="px", keyframeable=True)},
        "preview": {"kind": "css", "template": "blur({radius}px)"},
        "ffmpeg": {"filter": "gblur", "template": "gblur=sigma={radius}"},
    },
    "brightness": {
        "name": "亮度", "category": "color", "scope": "video", "icon": "sun",
        "params": {"value": _p("number", 0.0, min=-1.0, max=1.0, step=0.01,
                               keyframeable=True)},
        "preview": {"kind": "css", "template": "brightness({value_css})"},
        "ffmpeg": {"filter": "eq", "template": "eq=brightness={value}"},
    },
    "contrast": {
        "name": "对比度", "category": "color", "scope": "video", "icon": "contrast",
        "params": {"value": _p("number", 1.0, min=0.0, max=4.0, step=0.01,
                               keyframeable=True)},
        "preview": {"kind": "css", "template": "contrast({value})"},
        "ffmpeg": {"filter": "eq", "template": "eq=contrast={value}"},
    },
    "saturation": {
        "name": "饱和度", "category": "color", "scope": "video", "icon": "palette",
        "params": {"value": _p("number", 1.0, min=0.0, max=4.0, step=0.01,
                               keyframeable=True)},
        "preview": {"kind": "css", "template": "saturate({value})"},
        "ffmpeg": {"filter": "eq", "template": "eq=saturation={value}"},
    },
    "hue": {
        "name": "色相旋转", "category": "color", "scope": "video", "icon": "rotate",
        "params": {"degrees": _p("number", 0.0, min=-180.0, max=180.0, step=1.0,
                                 unit="deg", keyframeable=True)},
        "preview": {"kind": "css", "template": "hue-rotate({degrees}deg)"},
        "ffmpeg": {"filter": "hue", "template": "hue=h={degrees}"},
    },
    "grayscale": {
        "name": "去色", "category": "color", "scope": "video", "icon": "circle-half",
        "params": {"amount": _p("number", 1.0, min=0.0, max=1.0, step=0.01,
                                keyframeable=True)},
        "preview": {"kind": "css", "template": "grayscale({amount})"},
        "ffmpeg": {"filter": "hue", "template": "hue=s={amount_inv}"},
    },
    "sharpen": {
        "name": "锐化", "category": "filter", "scope": "video", "icon": "sparkles",
        "params": {"amount": _p("number", 0.0, min=0.0, max=10.0, step=0.1,
                                keyframeable=True)},
        "preview": {"kind": "canvas", "op": "sharpen"},
        "ffmpeg": {"filter": "unsharp", "template": "unsharp=5:5:{amount}"},
    },
    "vignette": {
        "name": "暗角", "category": "stylize", "scope": "video", "icon": "aperture",
        "params": {"angle": _p("number", 0.6, min=0.0, max=1.5, step=0.01,
                               keyframeable=True),
                   "color": _p("color", "#000000", keyframeable=False)},
        "preview": {"kind": "canvas", "op": "vignette"},
        "ffmpeg": {"filter": "vignette", "template": "vignette=angle={angle}"},
    },
    "audio_gain": {
        "name": "音频增益", "category": "audio", "scope": "audio", "icon": "volume",
        "params": {"db": _p("number", 0.0, min=-60.0, max=24.0, step=0.1,
                            unit="dB", keyframeable=True)},
        "preview": {"kind": "webaudio", "node": "gain", "template": "db_to_linear({db})"},
        "ffmpeg": {"filter": "volume", "template": "volume={db}dB"},
    },
    "audio_echo": {
        "name": "回声", "category": "audio", "scope": "audio", "icon": "waves",
        "params": {"delay": _p("number", 300.0, min=0.0, max=2000.0, step=10.0,
                               unit="ms", keyframeable=False),
                   "decay": _p("number", 0.4, min=0.0, max=1.0, step=0.01,
                               keyframeable=False)},
        "preview": {"kind": "webaudio", "node": "delay"},
        "ffmpeg": {"filter": "aecho", "template": "aecho=0.8:0.9:{delay}:{decay}"},
    },
}


def _path() -> str:
    p = cfg.data_dir("effects")
    return os.path.join(p, "registry.json").replace("\\", "/")


def on_change(fn: Callable[[Dict[str, Any]], None]) -> None:
    with _LOCK:
        _CHANGE_HANDLERS.append(fn)


def _emit() -> None:
    snap = snapshot()
    for fn in list(_CHANGE_HANDLERS):
        try:
            fn(snap)
        except Exception:
            pass


def _load() -> None:
    global _REG, _LOADED
    with _LOCK:
        reg: Dict[str, Any] = {"version": 1, "effects": {}, "updated": time.time()}
        p = _path()
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("effects"), dict):
                    reg = raw
            except (OSError, ValueError):
                pass
        custom = reg.get("effects") or {}
        merged: Dict[str, Any] = {}
        for k, v in BUILTIN.items():
            d = copy.deepcopy(v)
            d["type"] = k
            d["builtin"] = True
            d["source"] = "builtin"
            if k in custom and custom[k].get("disabled"):
                d["disabled"] = True
            merged[k] = d
        for k, v in custom.items():
            if k in merged:
                continue
            v = copy.deepcopy(v)
            v["type"] = k
            v.setdefault("builtin", False)
            v.setdefault("source", "agent")
            v.setdefault("params", {})
            merged[k] = v
        reg["effects"] = merged
        _REG = reg
        _LOADED = True


def _save() -> None:
    with _LOCK:
        p = _path()
        payload = {
            "version": _REG.get("version", 1),
            "updated": time.time(),
            "effects": {k: v for k, v in _REG["effects"].items()
                        if v.get("source") != "builtin"},
        }
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def reload_registry() -> None:
    _load()
    _emit()


def list_effects(scope: Optional[str] = None,
                 include_disabled: bool = False) -> List[Dict[str, Any]]:
    out = []
    for k, v in sorted(_REG["effects"].items(),
                       key=lambda kv: (kv[1].get("category", ""), kv[0])):
        if v.get("disabled") and not include_disabled:
            continue
        if scope and v.get("scope", "video") not in (scope, "both"):
            continue
        out.append(v)
    return out


def get_effect(effect_type: str) -> Optional[Dict[str, Any]]:
    return _REG["effects"].get(effect_type)


def snapshot() -> Dict[str, Any]:
    return {
        "version": _REG.get("version", 1),
        "updated": _REG.get("updated"),
        "effects": copy.deepcopy(_REG["effects"]),
    }


def _validate(etype: str, d: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise ValueError("效果定义必须是对象")
    out = copy.deepcopy(d)
    out["type"] = etype
    out["name"] = str(out.get("name") or etype)
    out["category"] = str(out.get("category") or "custom")
    scope = out.get("scope") or "video"
    if scope not in ("video", "audio", "both"):
        raise ValueError("scope 必须是 video/audio/both")
    out["scope"] = scope
    params = out.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    norm: Dict[str, Any] = {}
    for pname, spec in params.items():
        if not isinstance(spec, dict):
            spec = {"type": "number", "default": spec}
        t = spec.get("type", "number")
        if t not in PARAM_TYPES:
            t = "number"
        s = dict(spec)
        s["type"] = t
        s.setdefault("default", 0.0 if t in ("number", "int") else (False if t == "bool" else ""))
        s.setdefault("keyframeable", t in ("number", "int"))
        if t == "select" and not isinstance(s.get("options"), list):
            raise ValueError(f"参数 {pname} 为 select 但缺少 options")
        norm[pname] = s
    out["params"] = norm
    out.setdefault("preview", {"kind": "css", "template": ""})
    out.setdefault("ffmpeg", {"filter": "", "template": ""})
    out["builtin"] = False
    out.setdefault("source", "agent")
    out["registered"] = time.time()
    return out


def register(etype: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    etype = str(etype).strip()
    if not etype or not etype.replace("_", "").replace("-", "").isalnum():
        raise ValueError("效果 ID 只能包含字母数字下划线短横线")
    if etype in BUILTIN:
        raise ValueError(f"内置效果 {etype} 不可覆盖，请用新 ID")
    d = _validate(etype, definition)
    with _LOCK:
        _REG["effects"][etype] = d
    _save()
    _emit()
    return d


def update(etype: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    if etype in BUILTIN:
        raise ValueError("内置效果不可修改")
    if etype not in _REG["effects"]:
        raise KeyError(f"效果不存在: {etype}")
    d = _validate(etype, {**_REG["effects"][etype], **definition})
    with _LOCK:
        _REG["effects"][etype] = d
    _save()
    _emit()
    return d


def unregister(etype: str) -> bool:
    if etype in BUILTIN:
        raise ValueError("内置效果不可删除")
    with _LOCK:
        if etype not in _REG["effects"]:
            return False
        _REG["effects"].pop(etype, None)
    _save()
    _emit()
    return True


def default_params(etype: str) -> Dict[str, Any]:
    e = get_effect(etype)
    if not e:
        raise KeyError(f"效果不存在: {etype}")
    return {k: {"static": v.get("default")} for k, v in (e.get("params") or {}).items()}


def instantiate(etype: str, effect_id: Optional[str] = None,
                params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    e = get_effect(etype)
    if not e:
        raise KeyError(f"效果不存在: {etype}")
    base = default_params(etype)
    for k, v in (params or {}).items():
        base[k] = v if (isinstance(v, dict) and ("static" in v or "keys" in v)) else {"static": v}
    return {
        "id": effect_id or f"fx_{uuid.uuid4().hex[:8]}",
        "type": etype,
        "enabled": True,
        "params": base,
        "meta": {"name": e.get("name", etype), "category": e.get("category", "custom")},
    }


_load()


# __EOF__