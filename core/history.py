# -*- coding: utf-8 -*-
"""撤销 / 重做。记录动作组，每组含 undo 与 redo 两份状态快照。"""
import threading
import time
from typing import Any, Dict, List, Optional

from . import config as cfg

_LOCK = threading.RLock()


class _Stack:
    def __init__(self, limit: int) -> None:
        self.undo: List[Dict[str, Any]] = []
        self.redo: List[Dict[str, Any]] = []
        self.limit = limit

    def push(self, entry: Dict[str, Any]) -> None:
        self.undo.append(entry)
        if len(self.undo) > self.limit:
            self.undo = self.undo[-self.limit:]
        self.redo.clear()

    def pop_undo(self) -> Optional[Dict[str, Any]]:
        if not self.undo:
            return None
        e = self.undo.pop()
        self.redo.append(e)
        if len(self.redo) > self.limit:
            self.redo = self.redo[-self.limit:]
        return e

    def pop_redo(self) -> Optional[Dict[str, Any]]:
        if not self.redo:
            return None
        e = self.redo.pop()
        self.undo.append(e)
        return e

    def info(self) -> Dict[str, Any]:
        return {
            "undo": len(self.undo),
            "redo": len(self.redo),
            "undo_labels": [e.get("label") for e in self.undo[-10:]],
            "redo_labels": [e.get("label") for e in self.redo[-10:]],
        }


_STACKS: Dict[str, _Stack] = {}


def _get(pid: str) -> _Stack:
    with _LOCK:
        st = _STACKS.get(pid)
        if st is None:
            st = _Stack(int(cfg.get("history_limit", 200)))
            _STACKS[pid] = st
        return st


def record(pid: str, label: str,
           undo: List[Dict[str, Any]], redo: List[Dict[str, Any]],
           client_id: Optional[str] = None) -> None:
    entry = {
        "id": f"h_{int(time.time() * 1000)}_{len(undo)}",
        "label": label,
        "ts": time.time(),
        "undo": undo,
        "redo": redo,
        "client": client_id,
    }
    _get(pid).push(entry)


def has_undo(pid: str) -> bool:
    return bool(_get(pid).undo)


def has_redo(pid: str) -> bool:
    return bool(_get(pid).redo)


def pop_undo(pid: str) -> Optional[Dict[str, Any]]:
    return _get(pid).pop_undo()


def pop_redo(pid: str) -> Optional[Dict[str, Any]]:
    return _get(pid).pop_redo()


def info(pid: str) -> Dict[str, Any]:
    return _get(pid).info()


def clear(pid: str) -> None:
    with _LOCK:
        _STACKS.pop(pid, None)


# __EOF__