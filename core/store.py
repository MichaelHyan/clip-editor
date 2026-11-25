# -*- coding: utf-8 -*-
"""磁盘持久化：项目 / 片段 / 片段库 / 快照。全部原子写。"""
import json
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import project as project_mod

_LOCK = threading.RLock()


def _write_json(path: str, data: Any) -> str:
    path = path.replace("\\", "/")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _read_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _safe_id(s: str) -> str:
    keep = "".join(ch for ch in str(s) if ch.isalnum() or ch in "-_")
    if not keep:
        raise ValueError("非法 ID")
    return keep


def projects_root() -> str:
    p = os.path.join(cfg.ROOT, "data", "projects").replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def project_dir(pid: str) -> str:
    p = os.path.join(projects_root(), _safe_id(pid)).replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def clips_dir(pid: str) -> str:
    p = os.path.join(project_dir(pid), "clips").replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def snapshots_dir(pid: str) -> str:
    p = os.path.join(project_dir(pid), "snapshots").replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def library_dir() -> str:
    p = os.path.join(cfg.ROOT, "data", "library").replace("\\", "/")
    os.makedirs(p, exist_ok=True)
    return p


def list_projects() -> List[Dict[str, Any]]:
    out = []
    root = projects_root()
    for name in sorted(os.listdir(root)):
        pdir = os.path.join(root, name).replace("\\", "/")
        if not os.path.isdir(pdir):
            continue
        pj = _read_json(os.path.join(pdir, "project.json"))
        if not pj:
            continue
        idx = _read_json(os.path.join(pdir, "clips_index.json")) or {}
        out.append({
            "id": pj.get("id", name),
            "name": pj.get("name", "未命名项目"),
            "settings": pj.get("settings", {}),
            "rev": pj.get("rev", 1),
            "clip_count": len(idx),
            "updated": (pj.get("meta") or {}).get("updated", 0),
            "created": (pj.get("meta") or {}).get("created", 0),
            "dir": pdir,
        })
    out.sort(key=lambda x: x.get("updated", 0), reverse=True)
    return out


def save_project(pj: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        pid = _safe_id(pj["id"])
        pj = project_mod.normalize(pj)
        pj["meta"]["updated"] = time.time()
        _write_json(os.path.join(project_dir(pid), "project.json"), pj)
        return pj


def load_project(pid: str) -> Optional[Dict[str, Any]]:
    pj = _read_json(os.path.join(project_dir(pid), "project.json"))
    if pj is None:
        return None
    return project_mod.normalize(pj)


def delete_project(pid: str) -> bool:
    pdir = project_dir(pid)
    if not os.path.isdir(pdir):
        return False
    try:
        shutil.rmtree(pdir)
        return True
    except OSError:
        return False


def create_project(name: str, settings: Optional[Dict[str, Any]] = None,
                   author: str = "ui") -> Dict[str, Any]:
    pj = project_mod.make_project(name, settings, author)
    save_project(pj)
    _write_json(os.path.join(project_dir(pj["id"]), "clips_index.json"), {})
    return pj


def clip_path(pid: str, clip_id: str) -> str:
    return os.path.join(clips_dir(pid), _safe_id(clip_id) + ".json").replace("\\", "/")


def save_clip(pid: str, clip: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        path = clip_path(pid, clip["id"])
        _write_json(path, clip)
        idx_path = os.path.join(project_dir(pid), "clips_index.json").replace("\\", "/")
        idx = _read_json(idx_path) or {}
        idx[clip["id"]] = {
            "name": clip.get("name"),
            "kind": (clip.get("media") or {}).get("kind"),
            "updated": (clip.get("meta") or {}).get("updated", time.time()),
        }
        _write_json(idx_path, idx)
        return clip


def load_clip(pid: str, clip_id: str) -> Optional[Dict[str, Any]]:
    from . import clip as clip_mod
    c = _read_json(clip_path(pid, clip_id))
    if c is None:
        return None
    return clip_mod.normalize(c)


def list_clip_ids(pid: str) -> List[str]:
    d = clips_dir(pid)
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".json"))


def load_all_clips(pid: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for cid in list_clip_ids(pid):
        c = load_clip(pid, cid)
        if c:
            out[cid] = c
    return out


def delete_clip_file(pid: str, clip_id: str) -> bool:
    with _LOCK:
        p = clip_path(pid, clip_id)
        ok = False
        if os.path.isfile(p):
            try:
                os.remove(p)
                ok = True
            except OSError:
                ok = False
        idx_path = os.path.join(project_dir(pid), "clips_index.json").replace("\\", "/")
        idx = _read_json(idx_path) or {}
        if clip_id in idx:
            idx.pop(clip_id, None)
            _write_json(idx_path, idx)
        return ok


def library_save(clip: Dict[str, Any], name: Optional[str] = None) -> str:
    cid = _safe_id(clip.get("id", "clip"))
    data = dict(clip)
    if name:
        data["name"] = name
    data["meta"] = dict(data.get("meta") or {})
    data["meta"]["library"] = True
    data["meta"]["saved"] = time.time()
    path = os.path.join(library_dir(), cid + ".clip.json").replace("\\", "/")
    _write_json(path, data)
    return path


def library_list() -> List[Dict[str, Any]]:
    out = []
    d = library_dir()
    for f in sorted(os.listdir(d)):
        if not f.endswith(".clip.json"):
            continue
        c = _read_json(os.path.join(d, f))
        if not c:
            continue
        out.append({
            "id": c.get("id", os.path.splitext(f)[0]),
            "name": c.get("name"),
            "kind": (c.get("media") or {}).get("kind"),
            "media": (c.get("media") or {}).get("path"),
            "saved": (c.get("meta") or {}).get("saved", 0),
        })
    out.sort(key=lambda x: x.get("saved", 0), reverse=True)
    return out


def library_load(clip_id: str) -> Optional[Dict[str, Any]]:
    from . import clip as clip_mod
    c = _read_json(os.path.join(library_dir(), _safe_id(clip_id) + ".clip.json"))
    return clip_mod.normalize(c) if c else None


def library_delete(clip_id: str) -> bool:
    p = os.path.join(library_dir(), _safe_id(clip_id) + ".clip.json")
    if os.path.isfile(p):
        try:
            os.remove(p)
            return True
        except OSError:
            return False
    return False


def take_snapshot(pid: str, tag: str = "auto") -> Optional[str]:
    pdir = project_dir(pid)
    pj = _read_json(os.path.join(pdir, "project.json"))
    if not pj:
        return None
    snap = {"tag": tag, "taken": time.time(), "project": pj,
            "clips": load_all_clips(pid)}
    name = time.strftime("%Y%m%d_%H%M%S") + f"_{tag}.json"
    path = os.path.join(snapshots_dir(pid), name).replace("\\", "/")
    _write_json(path, snap)
    _prune_snapshots(pid, keep=40)
    return path


def list_snapshots(pid: str) -> List[Dict[str, Any]]:
    d = snapshots_dir(pid)
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if not f.endswith(".json"):
            continue
        p = os.path.join(d, f).replace("\\", "/")
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        out.append({"file": f, "path": p, "size": size})
    return out


def _prune_snapshots(pid: str, keep: int = 40) -> None:
    d = snapshots_dir(pid)
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    if len(files) <= keep:
        return
    for f in files[:len(files) - keep]:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass


def restore_snapshot(pid: str, filename: str) -> bool:
    d = snapshots_dir(pid)
    p = os.path.join(d, os.path.basename(filename)).replace("\\", "/")
    snap = _read_json(p)
    if not snap or "project" not in snap:
        return False
    save_project(snap["project"])
    for cid, c in (snap.get("clips") or {}).items():
        save_clip(pid, c)
    return True


# __EOF__