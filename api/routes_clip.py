# -*- coding: utf-8 -*-
"""片段接口：导入 / 修改 / 切分 / 关键帧 / 效果 / 片段库。"""
import copy
import time
import uuid
from typing import Any, Dict, Optional

from flask import Blueprint

from core import clip as clip_mod
from core import effects as effects_mod
from core import keyframe as kf
from core import media as media_mod
from core import ops as ops_mod
from core import project as project_mod
from core import realtime, store, workspace

from . import common as C

bp = Blueprint("clips", __name__)


def _state(cid: str, clip: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {"target": "clip", "id": cid, "state": copy.deepcopy(clip)}


def _pick_track(ws: Any, kind: str) -> Optional[str]:
    want = "audio" if kind == "audio" else "video"
    for tr in ws.project.get("tracks", []):
        if tr.get("type") == want:
            return tr["id"]
    tracks = ws.project.get("tracks") or []
    return tracks[0]["id"] if tracks else None


@bp.route("/<pid>/clips", methods=["POST"])
@C.wrap
def import_clip(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    path = C.check_path(b.get("path"))
    info = media_mod.probe(path)
    if not info.get("ok"):
        return C.err("媒体探测失败: " + str(info.get("error")), 400)
    if info.get("kind") not in ("video", "audio", "image"):
        return C.err("不支持的媒体类型")
    with ws.lock:
        track_id = b.get("track_id") or _pick_track(ws, info["kind"])
        if not track_id:
            return C.err("项目中无可用轨道，请先创建轨道")
        clip = clip_mod.make_clip(
            {"path": path, "kind": info["kind"], "probe": info,
             "name": info.get("name"), "duration": info.get("duration")},
            name=b.get("name"), track_id=track_id,
            start=float(b.get("start") or 0.0))
        dur = clip_mod.duration_of(clip)
        clip["timeline"]["duration"] = dur
        if info["kind"] == "image" and not dur:
            clip["timeline"]["duration"] = 5.0
        clip = clip_mod.normalize(clip)
        clip["timeline"]["start"] = ws.find_free_start(
            track_id, float(b.get("start") or 0.0), float(dur or 0.0))

        before_pj = copy.deepcopy(ws.project)
        ws.project = project_mod.attach_clip(ws.project, track_id, clip["id"])
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_clip(clip)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("导入片段 " + str(clip.get("name")),
                  [{"target": "project", "state": before_pj}, _state(clip["id"], None)],
                  [{"target": "project", "state": after_pj}, _state(clip["id"], clip)],
                  C.client_id())
        realtime.emit_clip(pid, clip, "create", C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())
        if not info.get("browser_ok", True):
            import threading as _threading
            def _warm(src=path):
                try:
                    res = media_mod.ensure_proxy(src, False)
                    realtime.broadcast_agent_event("proxy.done", {"path": src, "result": res})
                except Exception:
                    pass
            _threading.Thread(target=_warm, daemon=True).start()
        return C.ok(clip)


@bp.route("/<pid>/clips", methods=["GET"])
@C.wrap
def list_clips(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        return C.ok({"clips": ws.clips, "durations": ws.durations()})


@bp.route("/<pid>/clips/<cid>", methods=["GET"])
@C.wrap
def get_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        return C.ok(clip)


@bp.route("/<pid>/clips/<cid>/resolve", methods=["GET"])
@C.wrap
def resolve_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    t = C.argf("t", 0.0)
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        return C.ok({
            "clip_id": cid, "t_local": t,
            "props": clip_mod.resolve_at(clip, t),
            "source_time": clip_mod.source_time_at(clip, t),
            "duration": clip_mod.duration_of(clip),
        })


def _get_path(node: Any, path: str) -> Any:
    cur = node
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


@bp.route("/<pid>/clips/<cid>/bake", methods=["GET"])
@C.wrap
def bake_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    path = C.arg("path")
    if not path:
        return C.err("缺少 path 参数")
    t0 = C.argf("t0", 0.0)
    t1 = C.argf("t1", -1.0)
    fps = C.argf("fps", float(ws.project["settings"].get("fps", 60)))
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        if t1 < 0:
            t1 = clip_mod.duration_of(clip) or 0.0
        track = _get_path(clip, path)
        if track is None:
            return C.err("路径不存在: " + path, 404)
        return C.ok({"path": path, "samples": kf.bake(track, t0, t1, fps)})


@bp.route("/<pid>/clips/<cid>", methods=["PATCH"])
@C.wrap
def patch_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    patch: Dict[str, Any] = b.get("patch") or {}
    if not isinstance(patch, dict) or not patch:
        return C.err("patch 不能为空")
    rev = b.get("rev")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        if rev is not None and int(rev) != int(clip.get("rev", 1)):
            return C.err("版本冲突，请先同步", 409, rev=clip.get("rev", 1), clip=clip)
        before = copy.deepcopy(clip)
        newc = clip_mod.apply_patch(clip, patch)
        # 改倍速/裁剪入出点后，时间轴时长必须跟着重算
        if any(k in patch for k in (
                "video.speed", "audio.speed",
                "video.src_in", "video.src_out",
                "audio.src_in", "audio.src_out")):
            sd = clip_mod.source_duration(newc)
            if sd is not None:
                newc["timeline"]["duration"] = sd
        newc["rev"] = int(clip.get("rev", 1)) + 1
        newc = clip_mod.normalize(newc)
        ws.save_clip(newc)
        ws.commit("修改片段 " + str(newc.get("name")),
                  [_state(cid, before)], [_state(cid, newc)], C.client_id())
        realtime.emit_clip_patch(pid, cid, patch, newc["rev"], C.client_id())
        return C.ok(newc)


@bp.route("/<pid>/clips/<cid>", methods=["DELETE"])
@C.wrap
def delete_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before_pj = copy.deepcopy(ws.project)
        ws.project = project_mod.detach_clip(ws.project, cid)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.drop_clip(cid)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("删除片段",
                  [{"target": "project", "state": before_pj}, _state(cid, clip)],
                  [{"target": "project", "state": after_pj}, _state(cid, None)],
                  C.client_id())
        realtime.emit_clip_removed(pid, cid, C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())
        return C.ok({"deleted": cid})


@bp.route("/<pid>/clips/<cid>/duplicate", methods=["POST"])
@C.wrap
def duplicate_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        newc = clip_mod.normalize(ops_mod.duplicate(clip, b.get("name")))
        track_id = (b.get("track_id") or clip["timeline"].get("track_id")
                    or _pick_track(ws, clip["media"]["kind"]))
        newc["timeline"]["track_id"] = track_id
        want_start = (float(b.get("start")) if b.get("start") is not None
                      else float((newc.get("timeline") or {}).get("start") or 0.0))
        newc["timeline"]["start"] = ws.find_free_start(
            track_id, want_start, float(clip_mod.duration_of(newc) or 0.0))
        before_pj = copy.deepcopy(ws.project)
        ws.project = project_mod.attach_clip(ws.project, track_id, newc["id"])
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_clip(newc)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("复制片段",
                  [{"target": "project", "state": before_pj}, _state(newc["id"], None)],
                  [{"target": "project", "state": after_pj}, _state(newc["id"], newc)],
                  C.client_id())
        realtime.emit_clip(pid, newc, "create", C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())
        return C.ok(newc)


@bp.route("/<pid>/clips/<cid>/split", methods=["POST"])
@C.wrap
def split_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    t = b.get("t")
    if t is None:
        return C.err("缺少切分时间 t")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        a, bb = ops_mod.split(clip, float(t))
        a = clip_mod.normalize(a)
        b_clip = clip_mod.normalize(bb)
        track_id = clip["timeline"].get("track_id") or _pick_track(ws, clip["media"]["kind"])
        a["timeline"]["track_id"] = track_id
        b_clip["timeline"]["track_id"] = track_id

        before_pj = copy.deepcopy(ws.project)
        ws.save_clip(a)
        ws.save_clip(b_clip)
        ws.project = project_mod.detach_clip(ws.project, cid)
        ws.project = project_mod.attach_clip(ws.project, track_id, a["id"])
        ws.project = project_mod.attach_clip(ws.project, track_id, b_clip["id"])
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.drop_clip(cid)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("切分片段",
                  [{"target": "project", "state": before_pj}, _state(cid, before),
                   _state(a["id"], None), _state(b_clip["id"], None)],
                  [{"target": "project", "state": after_pj}, _state(cid, None),
                   _state(a["id"], a), _state(b_clip["id"], b_clip)],
                  C.client_id())
        realtime.emit_clip_removed(pid, cid, C.client_id())
        realtime.emit_clip(pid, a, "create", C.client_id())
        realtime.emit_clip(pid, b_clip, "create", C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())
        return C.ok({"a": a, "b": b_clip})


@bp.route("/<pid>/clips/<cid>/trim", methods=["POST"])
@C.wrap
def trim_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        newc = clip_mod.normalize(ops_mod.trim(clip, b.get("src_in"), b.get("src_out")))
        newc["timeline"]["duration"] = clip_mod.duration_of(newc)
        ws.save_clip(newc)
        ws.commit("修剪片段", [_state(cid, before)], [_state(cid, newc)], C.client_id())
        realtime.emit_clip(pid, newc, "update", C.client_id())
        return C.ok(newc)


@bp.route("/<pid>/clips/<cid>/move", methods=["POST"])
@C.wrap
def move_clip(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    to_track = b.get("track_id")
    if not to_track:
        return C.err("缺少 track_id")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        tr = project_mod.find_track(ws.project, to_track)
        if tr is None:
            return C.err("目标轨道不存在", 404)
        before_pj = copy.deepcopy(ws.project)
        before_clip = copy.deepcopy(clip)
        ws.project = project_mod.move_clip(ws.project, cid, to_track)
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        clip["timeline"]["track_id"] = to_track
        want = (float(b.get("start")) if b.get("start") is not None
                else float((clip.get("timeline") or {}).get("start") or 0.0))
        clip["timeline"]["start"] = ws.find_free_start(
            to_track, want, float(clip_mod.duration_of(clip) or 0.0),
            exclude_id=cid)
        clip["rev"] = int(clip.get("rev", 1)) + 1
        ws.save_clip(clip)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("移动片段",
                  [{"target": "project", "state": before_pj}, _state(cid, before_clip)],
                  [{"target": "project", "state": after_pj}, _state(cid, clip)],
                  C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())
        return C.ok(clip)


@bp.route("/<pid>/clips/<cid>/keyframe", methods=["POST"])
@C.wrap
def add_keyframe(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    path = b.get("path")
    if not path:
        return C.err("缺少 path")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        newc = clip_mod.add_keyframe(clip, path, float(b.get("t") or 0.0),
                                     b.get("value"), b.get("e") or "linear", b.get("h"))
        newc["rev"] = int(clip.get("rev", 1)) + 1
        ws.save_clip(newc)
        ws.commit("添加关键帧 " + path,
                  [_state(cid, before)], [_state(cid, newc)], C.client_id())
        realtime.emit_clip(pid, newc, "update", C.client_id())
        return C.ok(newc)


@bp.route("/<pid>/clips/<cid>/keyframe", methods=["DELETE"])
@C.wrap
def del_keyframe(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    path = b.get("path") or C.arg("path")
    t = b.get("t", C.argf("t", 0.0))
    if not path:
        return C.err("缺少 path")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        newc = clip_mod.remove_keyframe(clip, path, float(t))
        newc["rev"] = int(clip.get("rev", 1)) + 1
        ws.save_clip(newc)
        ws.commit("删除关键帧 " + path,
                  [_state(cid, before)], [_state(cid, newc)], C.client_id())
        realtime.emit_clip(pid, newc, "update", C.client_id())
        return C.ok(newc)


@bp.route("/<pid>/clips/<cid>/effects", methods=["POST"])
@C.wrap
def add_effect(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    etype = b.get("type")
    if not etype:
        return C.err("缺少效果 type")
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        eff = effects_mod.instantiate(etype, b.get("effect_id"), b.get("params"))
        if b.get("enabled") is False:
            eff["enabled"] = False
        before = copy.deepcopy(clip)
        clip.setdefault("effects", []).append(eff)
        clip["rev"] = int(clip.get("rev", 1)) + 1
        clip["meta"]["updated"] = time.time()
        clip = clip_mod.normalize(clip)
        ws.save_clip(clip)
        ws.commit("添加效果 " + str(eff["meta"].get("name")),
                  [_state(cid, before)], [_state(cid, clip)], C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        return C.ok({"effect": eff, "clip": clip})


@bp.route("/<pid>/clips/<cid>/effects/<fxid>", methods=["PATCH"])
@C.wrap
def patch_effect(pid: str, cid: str, fxid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        target = None
        for eff in clip.get("effects", []):
            if eff.get("id") == fxid or eff.get("type") == fxid:
                target = eff
                break
        if target is None:
            return C.err("效果不存在", 404)
        before = copy.deepcopy(clip)
        if "enabled" in b:
            target["enabled"] = bool(b["enabled"])
        if isinstance(b.get("params"), dict):
            for pname, pval in b["params"].items():
                if isinstance(pval, dict) and ("static" in pval or "keys" in pval):
                    target["params"][pname] = pval
                else:
                    target["params"][pname] = {"static": pval}
        clip["rev"] = int(clip.get("rev", 1)) + 1
        clip["meta"]["updated"] = time.time()
        clip = clip_mod.normalize(clip)
        ws.save_clip(clip)
        ws.commit("修改效果参数",
                  [_state(cid, before)], [_state(cid, clip)], C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        return C.ok(clip)


@bp.route("/<pid>/clips/<cid>/effects/<fxid>", methods=["DELETE"])
@C.wrap
def delete_effect(pid: str, cid: str, fxid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        keep = [x for x in clip.get("effects", [])
                if x.get("id") != fxid and x.get("type") != fxid]
        if len(keep) == len(clip.get("effects", [])):
            return C.err("效果不存在", 404)
        clip["effects"] = keep
        clip["rev"] = int(clip.get("rev", 1)) + 1
        clip = clip_mod.normalize(clip)
        ws.save_clip(clip)
        ws.commit("删除效果",
                  [_state(cid, before)], [_state(cid, clip)], C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        return C.ok(clip)


@bp.route("/<pid>/clips/<cid>/markers", methods=["POST"])
@C.wrap
def add_clip_marker(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        clip.setdefault("markers", []).append({
            "id": "mk_" + uuid.uuid4().hex[:8],
            "t": float(b.get("t") or 0.0),
            "label": b.get("label") or "",
            "color": b.get("color") or "#ffcc00",
        })
        clip["markers"].sort(key=lambda m: m["t"])
        clip["rev"] = int(clip.get("rev", 1)) + 1
        clip = clip_mod.normalize(clip)
        ws.save_clip(clip)
        ws.commit("添加片段标记",
                  [_state(cid, before)], [_state(cid, clip)], C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        return C.ok(clip["markers"])


@bp.route("/<pid>/clips/<cid>/markers/<mid>", methods=["DELETE"])
@C.wrap
def del_clip_marker(pid: str, cid: str, mid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        before = copy.deepcopy(clip)
        clip["markers"] = [m for m in clip.get("markers", []) if m.get("id") != mid]
        clip["rev"] = int(clip.get("rev", 1)) + 1
        clip = clip_mod.normalize(clip)
        ws.save_clip(clip)
        ws.commit("删除片段标记",
                  [_state(cid, before)], [_state(cid, clip)], C.client_id())
        realtime.emit_clip(pid, clip, "update", C.client_id())
        return C.ok(clip["markers"])


@bp.route("/<pid>/clips/<cid>/save-to-library", methods=["POST"])
@C.wrap
def save_to_library(pid: str, cid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    with ws.lock:
        clip = ws.clips.get(cid)
        if clip is None:
            return C.err("片段不存在", 404)
        path = store.library_save(clip, b.get("name"))
        return C.ok({"path": path, "library": store.library_list()})


@bp.route("/<pid>/clips/from-library", methods=["POST"])
@C.wrap
def load_from_library(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    lib_id = b.get("id")
    if not lib_id:
        return C.err("缺少 id")
    src = store.library_load(lib_id)
    if src is None:
        return C.err("片段库中不存在: " + str(lib_id), 404)
    with ws.lock:
        clip = clip_mod.normalize(ops_mod.duplicate(src, b.get("name")))
        track_id = b.get("track_id") or _pick_track(ws, clip["media"]["kind"])
        clip["timeline"]["track_id"] = track_id
        clip["timeline"]["start"] = float(b.get("start") or 0.0)
        before_pj = copy.deepcopy(ws.project)
        ws.project = project_mod.attach_clip(ws.project, track_id, clip["id"])
        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_clip(clip)
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)
        ws.commit("从片段库导入",
                  [{"target": "project", "state": before_pj}, _state(clip["id"], None)],
                  [{"target": "project", "state": after_pj}, _state(clip["id"], clip)],
                  C.client_id())
        realtime.emit_clip(pid, clip, "create", C.client_id())
        return C.ok(clip)


@bp.route("/<pid>/library", methods=["GET"])
@C.wrap
def library_list(pid: str) -> Any:
    return C.ok(store.library_list())


# __EOF__