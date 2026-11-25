# -*- coding: utf-8 -*-
"""SocketIO 实例与广播封装。所有写操作后广播，让 UI 与 agent 双向同步。"""
import time
from typing import Any, Dict, Optional

try:
    from flask_socketio import SocketIO
    _HAS_SIO = True
except ImportError:
    SocketIO = None
    _HAS_SIO = False


class _NullIO:
    def emit(self, *a: Any, **kw: Any) -> None:
        return None

    def on(self, *a: Any, **kw: Any) -> Any:
        def deco(fn: Any) -> Any:
            return fn
        return deco

    def init_app(self, *a: Any, **kw: Any) -> None:
        return None

    def run(self, *a: Any, **kw: Any) -> None:
        raise RuntimeError("SocketIO 不可用")


sio = SocketIO(cors_allowed_origins="*", async_mode="threading") if _HAS_SIO else _NullIO()


def _emit(event: str, payload: Dict[str, Any], room: Optional[str] = None) -> None:
    payload = dict(payload)
    payload.setdefault("ts", time.time())
    try:
        if room:
            sio.emit(event, payload, to=room)
        else:
            sio.emit(event, payload)
    except Exception:
        pass


def emit_project(pid: str, project: Dict[str, Any], action: str = "update",
                 client_id: Optional[str] = None) -> None:
    _emit("project.updated", {"project_id": pid, "action": action,
                              "project": project, "client_id": client_id},
          room="proj:" + pid)


def emit_project_patch(pid: str, patch: Dict[str, Any],
                       client_id: Optional[str] = None) -> None:
    _emit("project.patched", {"project_id": pid, "patch": patch,
                              "client_id": client_id}, room="proj:" + pid)


def emit_clip(pid: str, clip: Dict[str, Any], action: str = "update",
              client_id: Optional[str] = None) -> None:
    _emit("clip.updated", {"project_id": pid, "action": action,
                           "clip": clip, "client_id": client_id},
          room="proj:" + pid)


def emit_clip_patch(pid: str, clip_id: str, patch: Dict[str, Any],
                    rev: int = 0, client_id: Optional[str] = None) -> None:
    _emit("clip.patched", {"project_id": pid, "clip_id": clip_id,
                           "patch": patch, "rev": rev, "client_id": client_id},
          room="proj:" + pid)


def emit_clip_removed(pid: str, clip_id: str,
                      client_id: Optional[str] = None) -> None:
    _emit("clip.removed", {"project_id": pid, "clip_id": clip_id,
                           "client_id": client_id}, room="proj:" + pid)


def emit_effects(changed: Optional[Dict[str, Any]] = None,
                 action: str = "update") -> None:
    from . import effects as effects_mod
    _emit("effect.registry.updated",
          {"action": action, "registry": effects_mod.snapshot(),
           "changed": changed})


def emit_playhead(pid: str, t: float, client_id: Optional[str] = None) -> None:
    _emit("playhead", {"project_id": pid, "t": t, "client_id": client_id},
          room="proj:" + pid)


def emit_history(pid: str, info: Dict[str, Any],
                 client_id: Optional[str] = None) -> None:
    _emit("history.updated", {"project_id": pid, "info": info,
                              "client_id": client_id}, room="proj:" + pid)


def join_room(pid: str) -> None:
    try:
        from flask import request
        from flask_socketio import join_room as _join
        sid = getattr(request, "sid", None)
        if sid:
            _join("proj:" + pid, sid=sid)
    except Exception:
        pass


def broadcast_agent_event(kind: str, payload: Dict[str, Any]) -> None:
    _emit("agent.event", {"kind": kind, **(payload or {})})


if _HAS_SIO:
    @sio.on("connect")
    def _on_connect(*_a: Any, **_kw: Any) -> None:
        _emit("server.hello", {"ok": True})

    @sio.on("join")
    def _on_join(data: Any) -> None:
        pid = (data or {}).get("project_id")
        if pid:
            join_room(str(pid))
            _emit("joined", {"project_id": pid})

    @sio.on("playhead")
    def _on_playhead(data: Any) -> None:
        data = data or {}
        pid = data.get("project_id")
        if pid:
            emit_playhead(str(pid), float(data.get("t", 0.0)), data.get("client_id"))


# __EOF__