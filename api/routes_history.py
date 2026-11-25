# -*- coding: utf-8 -*-
"""撤销 / 重做。"""
from typing import Any

from flask import Blueprint

from core import history, realtime, workspace

from . import common as C

bp = Blueprint("history", __name__)


@bp.route("/<pid>/history", methods=["GET"])
@C.wrap
def get_history(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    return C.ok(history.info(pid))


def _apply(pid: str, direction: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        entry = history.pop_undo(pid) if direction == "undo" else history.pop_redo(pid)
        if entry is None:
            return C.err("没有可" + ("撤销" if direction == "undo" else "重做") + "的操作", 400)
        states = entry["undo"] if direction == "undo" else entry["redo"]
        ws.apply_states(states)
        realtime.emit_project(pid, ws.project, direction, C.client_id())
        for st in states:
            if st.get("target") == "clip":
                cid = st.get("id")
                if st.get("state") is None:
                    realtime.emit_clip_removed(pid, cid, C.client_id())
                else:
                    realtime.emit_clip(pid, ws.clips.get(cid), "update", C.client_id())
        realtime.emit_history(pid, history.info(pid), C.client_id())
        return C.ok({"info": history.info(pid), "label": entry.get("label"),
                     "timeline": ws.timeline()})


@bp.route("/<pid>/undo", methods=["POST"])
@C.wrap
def undo(pid: str) -> Any:
    return _apply(pid, "undo")


@bp.route("/<pid>/redo", methods=["POST"])
@C.wrap
def redo(pid: str) -> Any:
    return _apply(pid, "redo")


# __EOF__
