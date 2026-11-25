# -*- coding: utf-8 -*-
"""项目 / 轨道 / 标记 / 快照接口。"""
import copy
import time
from typing import Any, Dict

from flask import Blueprint

from core import project as project_mod
from core import realtime, store, workspace

from . import common as C

bp = Blueprint("projects", __name__)


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@C.wrap
def list_projects() -> Any:
    return C.ok(workspace.list_all())


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
@C.wrap
def create_project() -> Any:
    b = C.body()
    ws = workspace.create(
        name=b.get("name") or "未命名项目",
        settings=b.get("settings"),
        author=b.get("author") or "ui",
    )
    realtime.emit_project(ws.pid, ws.project, "create", C.client_id())
    return C.ok(ws.timeline())


@bp.route("/<pid>", methods=["GET"])
@C.wrap
def get_project(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        return C.ok(ws.timeline())


_SETTINGS_ENUMS = {
    "proxy_mode": ("off", "auto", "always"),
}

_SETTINGS_LIMITS = {
    "width": (160, 7680),
    "height": (120, 4320),
    "fps": (1, 240),
    "sample_rate": (8000, 192000),
    "channels": (1, 8),
}


def _validate_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    for path, value in patch.items():
        if not path.startswith("settings."):
            continue
        key = path.split(".", 1)[1]
        enum = _SETTINGS_ENUMS.get(key)
        if enum:
            val = str(value or "").lower()
            if val not in enum:
                raise ValueError("设置项 %s 只能取 %s，收到 %s"
                                 % (key, "/".join(enum), value))
            patch[path] = val
            continue
        lim = _SETTINGS_LIMITS.get(key)
        if not lim:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError("设置项 %s 必须是数字" % key)
        lo, hi = lim
        if v < lo or v > hi:
            raise ValueError("设置项 %s 取值范围 %s~%s，收到 %s" % (key, lo, hi, value))
        if key in ("width", "height", "sample_rate", "channels"):
            patch[path] = int(v)
    return patch


@bp.route("/<pid>", methods=["PATCH"])
@C.wrap
def patch_project(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    patch: Dict[str, Any] = b.get("patch") or {}
    patch = _validate_settings(dict(patch))
    with ws.lock:
        before = copy.deepcopy(ws.project)
        pj = copy.deepcopy(ws.project)
        for path, value in patch.items():
            if path in ("id", "tracks", "rev", "meta.created"):
                continue
            parts = path.split(".")
            node = pj
            for p in parts[:-1]:
                if not isinstance(node.get(p), dict):
                    node[p] = {}
                node = node[p]
            node[parts[-1]] = value
        pj = project_mod.normalize(pj)
        pj["rev"] = int(before.get("rev", 1)) + 1
        ws.project = pj
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("修改项目属性",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, patch, C.client_id())
        realtime.emit_project(pid, after, "update", C.client_id())
        return C.ok(after)


@bp.route("/<pid>", methods=["DELETE"])
@C.wrap
def delete_project(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    workspace.close(pid)
    ok = store.delete_project(pid)
    return C.ok({"deleted": ok})


@bp.route("/<pid>/tracks", methods=["POST"])
@C.wrap
def add_track(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.add_track(
            ws.project, b.get("type") or "video", b.get("name"), b.get("index"))
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("新增轨道",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project(pid, after, "track_added", C.client_id())
        return C.ok(after)


@bp.route("/<pid>/tracks/<tid>", methods=["DELETE"])
@C.wrap
def delete_track(pid: str, tid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        tr = project_mod.find_track(ws.project, tid)
        if tr is None:
            return C.err("轨道不存在", 404)

        before = copy.deepcopy(ws.project)
        # 被删轨道上的片段必须在 drop_clip 之前快照进撤销栈，
        # 否则「删除轨道」永久不可逆：轨道能撤销回来，片段文件却已删。
        clip_ids = list(tr.get("clips") or [])
        undo_states = [{"target": "project", "state": before}]
        redo_states = []
        for cid in clip_ids:
            c = ws.clips.get(cid)
            if c is not None:
                undo_states.append({"target": "clip", "id": cid,
                                    "state": copy.deepcopy(c)})
            redo_states.append({"target": "clip", "id": cid, "state": None})
            ws.drop_clip(cid)

        ws.project = project_mod.remove_track(ws.project, tid)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)

        redo_states.insert(0, {"target": "project", "state": after})
        ws.commit("删除轨道", undo_states, redo_states, C.client_id())

        realtime.emit_project(pid, after, "track_removed", C.client_id())
        for cid in clip_ids:
            realtime.emit_clip_removed(pid, cid, C.client_id())
        return C.ok(after)


@bp.route("/<pid>/tracks/<tid>", methods=["PATCH"])
@C.wrap
def patch_track(pid: str, tid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    patch: Dict[str, Any] = b.get("patch") or {}
    with ws.lock:
        tr = project_mod.find_track(ws.project, tid)
        if tr is None:
            return C.err("轨道不存在", 404)
        before = copy.deepcopy(ws.project)
        allowed = {"name", "muted", "hidden", "locked", "height", "index", "type"}
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "height":
                tr[k] = max(28, min(240, int(v)))
            elif k == "index":
                tr[k] = int(v)
            elif k == "type":
                t = str(v or "")
                if t not in project_mod.TRACK_TYPES:
                    return C.err("非法轨道类型: " + t, 400)
                if t != tr.get("type") and (tr.get("clips") or []):
                    return C.err(
                        "轨道上还有 %d 个片段，不能改类型" % len(tr["clips"]), 400)
                tr[k] = t
            elif k == "name":
                nm = str(v or "").strip()
                if nm:
                    tr[k] = nm[:40]
            else:
                tr[k] = v
        ws.project["tracks"].sort(key=lambda t: t.get("index", 0))
        for i, t in enumerate(ws.project["tracks"]):
            t["index"] = i
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("修改轨道 " + str(tr.get("name")),
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project(pid, after, "track_updated", C.client_id())
        return C.ok(after)


@bp.route("/<pid>/tracks/reorder", methods=["POST"])
@C.wrap
def reorder_tracks(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    order = b.get("order") or []
    if not isinstance(order, list):
        return C.err("order 必须是数组")
    with ws.lock:
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.reorder_tracks(ws.project, [str(x) for x in order])
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("轨道排序",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project(pid, after, "tracks_reordered", C.client_id())
        return C.ok(after)


@bp.route("/<pid>/markers", methods=["POST"])
@C.wrap
def add_marker(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.add_marker(
            ws.project, float(b.get("t") or 0.0), b.get("label") or "",
            b.get("color") or "#ffcc00", b.get("track_id"))
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("添加标记",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, {"markers": after["markers"]}, C.client_id())
        return C.ok(after["markers"])


@bp.route("/<pid>/markers/<mid>", methods=["DELETE"])
@C.wrap
def delete_marker(pid: str, mid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.remove_marker(ws.project, mid)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("删除标记",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, {"markers": after["markers"]}, C.client_id())
        return C.ok(after["markers"])


# ---------------------------------------------------------------- 字幕

@bp.route("/<pid>/subtitles", methods=["GET"])
@C.wrap
def list_subtitles(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        return C.ok(ws.project.get("subtitles") or [])


@bp.route("/<pid>/subtitles", methods=["POST"])
@C.wrap
def add_subtitle(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        before = copy.deepcopy(ws.project)
        start = float(b.get("start") if b.get("start") is not None else 0.0)
        end = float(b.get("end") if b.get("end") is not None else start + 3.0)
        ws.project = project_mod.add_subtitle(
            ws.project, start, end, b.get("text") or "", b.get("style"))
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("添加字幕",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, {"subtitles": after["subtitles"]},
                                    C.client_id())
        subs = after["subtitles"]
        return C.ok(subs[-1] if subs else None)


@bp.route("/<pid>/subtitles/<sid>", methods=["PATCH"])
@C.wrap
def patch_subtitle(pid: str, sid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        if project_mod.find_subtitle(ws.project, sid) is None:
            return C.err("字幕不存在", 404)
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.update_subtitle(ws.project, sid, b)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("修改字幕",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, {"subtitles": after["subtitles"]},
                                    C.client_id())
        return C.ok(project_mod.find_subtitle(after, sid))


@bp.route("/<pid>/subtitles/<sid>", methods=["DELETE"])
@C.wrap
def del_subtitle(pid: str, sid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        if project_mod.find_subtitle(ws.project, sid) is None:
            return C.err("字幕不存在", 404)
        before = copy.deepcopy(ws.project)
        ws.project = project_mod.remove_subtitle(ws.project, sid)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after = copy.deepcopy(ws.project)
        ws.commit("删除字幕",
                  [{"target": "project", "state": before}],
                  [{"target": "project", "state": after}],
                  C.client_id())
        realtime.emit_project_patch(pid, {"subtitles": after["subtitles"]},
                                    C.client_id())
        return C.ok(after["subtitles"])


@bp.route("/<pid>/snapshot", methods=["POST"])
@C.wrap
def take_snapshot(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    path = store.take_snapshot(pid, b.get("tag") or "manual")
    return C.ok({"path": path, "snapshots": store.list_snapshots(pid)})


@bp.route("/<pid>/snapshots", methods=["GET"])
@C.wrap
def list_snapshots(pid: str) -> Any:
    return C.ok(store.list_snapshots(pid))


@bp.route("/<pid>/snapshot/restore", methods=["POST"])
@C.wrap
def restore_snapshot(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    name = b.get("file")
    if not name:
        return C.err("缺少 file 参数")
    ok = store.restore_snapshot(pid, name)
    if not ok:
        return C.err("快照恢复失败", 400)
    ws = workspace.open_project(pid, force=True)
    realtime.emit_project(pid, ws.project, "restored", C.client_id())
    return C.ok(ws.timeline())


# __EOF__