# -*- coding: utf-8 -*-
"""项目工作区：内存缓存 + 并发锁 + 变更记录。"""
import copy
import threading
import time
from typing import Any, Dict, List, Optional

from . import clip as clip_mod
from . import history, project as project_mod, realtime, store

_LOCK = threading.RLock()
_CACHE: Dict[str, "Workspace"] = {}


class Workspace:
    def __init__(self, project: Dict[str, Any]) -> None:
        self.lock = threading.RLock()
        self.project = project_mod.normalize(project)
        self.clips: Dict[str, Dict[str, Any]] = store.load_all_clips(project["id"])
        self.opened = time.time()

    @property
    def pid(self) -> str:
        return self.project["id"]

    def _layout(self) -> None:
        """轨道内片段按时间轴起点排序，保证 UI 与导出一致。"""
        for tr in self.project.get("tracks", []):
            ids = tr.get("clips") or []
            if len(ids) < 2:
                continue

            def _key(cid: str):
                c = self.clips.get(cid) or {}
                st = (c.get("timeline") or {}).get("start")
                return (0 if st is not None else 1, float(st or 0.0), str(cid))

            tr["clips"] = sorted(ids, key=_key)

    def save_project(self) -> Dict[str, Any]:
        self._layout()
        self.project = store.save_project(self.project)
        return self.project

    def save_clip(self, clip: Dict[str, Any]) -> Dict[str, Any]:
        self.clips[clip["id"]] = clip
        store.save_clip(self.pid, clip)
        return clip

    def drop_clip(self, clip_id: str) -> None:
        self.clips.pop(clip_id, None)
        store.delete_clip_file(self.pid, clip_id)

    def durations(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for cid, c in self.clips.items():
            d = clip_mod.duration_of(c)
            if d is not None:
                out[cid] = d
        return out

    def timeline(self) -> Dict[str, Any]:
        durs = self.durations()
        return {
            "project": self.project,
            "clips": self.clips,
            "durations": durs,
            "total_duration": project_mod.total_duration(self.project, durs),
            "history": history.info(self.pid),
        }

    def commit(self, label: str, undo: List[Dict[str, Any]],
               redo: List[Dict[str, Any]],
               client_id: Optional[str] = None) -> None:
        history.record(self.pid, label, undo, redo, client_id)
        realtime.emit_history(self.pid, history.info(self.pid), client_id)

    def apply_states(self, states: List[Dict[str, Any]]) -> None:
        for st in states or []:
            target = st.get("target")
            state = st.get("state")
            if target == "project":
                if state is None:
                    continue
                self.project = project_mod.normalize(state)
                store.save_project(self.project)
            elif target == "clip":
                cid = st.get("id")
                if not cid:
                    continue
                if state is None:
                    self.drop_clip(cid)
                else:
                    self.save_clip(clip_mod.normalize(state))

    # ------------------------------------------------------------ 布局

    def find_free_start(self, track_id: str, desired: float, dur: float,
                        exclude_id: Optional[str] = None) -> float:
        """在指定轨道上找第一个不与已有片段重叠的起点。"""
        if not track_id:
            return max(0.0, float(desired))
        tr = None
        for t in self.project.get("tracks", []):
            if t.get("id") == track_id:
                tr = t
                break
        if tr is None:
            return max(0.0, float(desired))

        ranges = []
        for cid in tr.get("clips") or []:
            if cid == exclude_id:
                continue
            c = self.clips.get(cid)
            if not c:
                continue
            st = float((c.get("timeline") or {}).get("start") or 0.0)
            d = clip_mod.duration_of(c) or 0.0
            ranges.append((st, st + d))
        ranges.sort()

        start = max(0.0, float(desired))
        length = max(0.0, float(dur or 0.0))
        for _ in range(64):
            end = start + length
            hit = None
            for a, b in ranges:
                if start < b - 1e-6 and end > a + 1e-6:
                    hit = (a, b)
                    break
            if hit is None:
                break
            start = hit[1]
        return round(max(0.0, start), 6)

    def clip_snapshot(self, clip_id: str) -> Dict[str, Any]:
        return {"target": "clip", "id": clip_id,
                "state": copy.deepcopy(self.clips.get(clip_id))}

    def project_snapshot(self) -> Dict[str, Any]:
        return {"target": "project", "state": copy.deepcopy(self.project)}


def open_project(pid: str, force: bool = False) -> Optional[Workspace]:
    with _LOCK:
        if not force and pid in _CACHE:
            return _CACHE[pid]
        pj = store.load_project(pid)
        if pj is None:
            return None
        ws = Workspace(pj)
        _CACHE[pid] = ws
        return ws


def get(pid: str) -> Optional[Workspace]:
    return open_project(pid)


def create(name: str, settings: Optional[Dict[str, Any]] = None,
           author: str = "ui") -> Workspace:
    pj = store.create_project(name, settings, author)
    with _LOCK:
        ws = Workspace(pj)
        _CACHE[pj["id"]] = ws
        return ws


def close(pid: str) -> None:
    with _LOCK:
        _CACHE.pop(pid, None)


def list_all() -> List[Dict[str, Any]]:
    return store.list_projects()


# __EOF__