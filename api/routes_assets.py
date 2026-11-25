# -*- coding: utf-8 -*-
"""素材箱接口：登记 / 列表 / 删除 / 上时间轴。"""
import copy
import time
from typing import Any, Dict, Optional

from flask import Blueprint

from core import assets as assets_mod
from core import clip as clip_mod
from core import ops as ops_mod
from core import project as project_mod
from core import realtime, workspace

from . import common as C

bp = Blueprint("assets", __name__)


def _pick_track(ws: Any, role: str) -> Optional[str]:
    want = "audio" if role == "audio" else "video"
    for tr in ws.project.get("tracks", []):
        if tr.get("type") == want:
            return tr["id"]
    for tr in ws.project.get("tracks", []):
        if tr.get("type") in ("video", "audio"):
            return tr["id"]
    return None


@bp.route("/<pid>/assets", methods=["GET"])
@C.wrap
def list_assets(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    return C.ok(assets_mod.list_assets(pid))


@bp.route("/<pid>/assets", methods=["POST"])
@C.wrap
def add_asset(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    path = C.check_path(b.get("path"))
    res = assets_mod.add(pid, path, b.get("name"))
    return C.ok({**res, "list": assets_mod.list_assets(pid)})


@bp.route("/<pid>/assets/<aid>", methods=["DELETE"])
@C.wrap
def remove_asset(pid: str, aid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    ok = assets_mod.remove(pid, aid)
    if not ok:
        return C.err("素材不存在", 404)
    return C.ok(assets_mod.list_assets(pid))


@bp.route("/<pid>/assets/refresh", methods=["POST"])
@C.wrap
def refresh_assets(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    n = assets_mod.refresh(pid, b.get("asset_id"))
    return C.ok({"updated": n, "list": assets_mod.list_assets(pid)})


@bp.route("/<pid>/assets/<aid>/to-timeline", methods=["POST"])
@C.wrap
def asset_to_timeline(pid: str, aid: str) -> Any:
    """把素材卡片放到时间轴，生成 clip。

    body: {role: video|audio|image, track_id?, start?, split_av?: bool}
    split_av=true 时视频卡同时生成视频+音频两个 clip。
    """
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    asset = assets_mod.find(pid, aid)
    if not asset:
        return C.err("素材不存在", 404)

    role = b.get("role") or "video"
    start = float(b.get("start") or 0.0)
    split_av = bool(b.get("split_av"))

    probe = asset.get("probe") or {}
    path = asset["path"]
    dur = probe.get("duration")

    # 截取区间（秒）。null 表示不裁剪
    src_in = b.get("src_in")
    src_out = b.get("src_out")
    src_in = float(src_in) if src_in is not None else None
    src_out = float(src_out) if src_out is not None else None
    if src_in is not None and src_in < 0:
        src_in = 0.0
    if src_out is not None and src_in is not None and src_out <= src_in:
        return C.err("出点必须大于入点")
    if src_out is not None and dur is not None and src_out > float(dur) + 1e-3:
        src_out = float(dur)
    trim_len = None
    if src_in is not None or src_out is not None:
        a = src_in if src_in is not None else 0.0
        bb = src_out if src_out is not None else (float(dur) if dur else a)
        trim_len = max(0.0, bb - a)

    made = []
    with ws.lock:
        before_pj = copy.deepcopy(ws.project)
        before_clips: Dict[str, Optional[Dict[str, Any]]] = {}

        def make(role_: str, start_override: Optional[float] = None) -> Optional[Dict[str, Any]]:
            kind = "audio" if role_ == "audio" else ("image" if role_ == "image" else "video")
            track_id = b.get("track_id") if not split_av else None
            if not track_id:
                track_id = _pick_track(ws, kind)
            if not track_id:
                return None
            base_start = start_override if start_override is not None else start
            c = clip_mod.make_clip(
                {"path": path, "kind": kind, "probe": probe,
                 "name": asset.get("name"), "duration": dur},
                name=b.get("name") or asset.get("name"),
                track_id=track_id, start=base_start)
            # 应用截取区间（视频段与音频段各自独立）
            if src_in is not None:
                c["video"]["src_in"] = {"static": src_in}
                c["audio"]["src_in"] = {"static": src_in}
            if src_out is not None:
                c["video"]["src_out"] = {"static": src_out}
                c["audio"]["src_out"] = {"static": src_out}
            d0 = clip_mod.duration_of(c)
            if trim_len is not None:
                spd = (c.get(kind if kind == "audio" else "video", {})
                       .get("speed", {}).get("static") or 1.0)
                try:
                    d0 = trim_len / (float(spd) or 1.0)
                except (TypeError, ValueError):
                    d0 = trim_len
            c["timeline"]["duration"] = d0 or dur
            if kind == "image" and not c["timeline"]["duration"]:
                c["timeline"]["duration"] = 5.0
            c = clip_mod.normalize(c)
            if start_override is None:
                # 未指定起点时，由后端找空位
                c["timeline"]["start"] = ws.find_free_start(
                    track_id, float(c["timeline"].get("start") or 0.0),
                    float(clip_mod.duration_of(c) or 0.0))
            else:
                c["timeline"]["start"] = round(float(start_override), 6)
            before_clips[c["id"]] = None
            ws.save_clip(c)
            ws.project = project_mod.attach_clip(ws.project, track_id, c["id"])
            return c

        if split_av and probe.get("has_audio") and role == "video":
            # 音视频必须同起点：先在两条轨上找共同空位
            vtr = b.get("track_id") or _pick_track(ws, "video")
            atr = _pick_track(ws, "audio")
            # 计算预计时长（截取后）
            probe_dur = float(dur or 0.0)
            if trim_len is not None:
                probe_dur = float(trim_len)
            common = start
            if vtr and atr:
                common = ws.find_free_start(vtr, start, probe_dur)
                common = ws.find_free_start(atr, common, probe_dur)
                # 再回头确认视频轨（防止推到音频空位后又撞视频）
                common = ws.find_free_start(vtr, common, probe_dur)
            v = make("video", start_override=common)
            a = make("audio", start_override=common)
            made = [x for x in (v, a) if x]
        else:
            c = make(role)
            made = [c] if c else []

        if not made:
            return C.err("没有可用轨道，请先创建轨道")

        ws.project["rev"] = int(ws.project.get("rev", 1)) + 1
        ws.save_project()
        after_pj = copy.deepcopy(ws.project)

        undo = [{"target": "project", "state": before_pj}]
        redo = [{"target": "project", "state": after_pj}]
        for c in made:
            undo.append({"target": "clip", "id": c["id"], "state": None})
            redo.append({"target": "clip", "id": c["id"], "state": copy.deepcopy(c)})
        ws.commit("素材上时间轴 " + str(asset.get("name")), undo, redo, C.client_id())

        for c in made:
            realtime.emit_clip(pid, c, "create", C.client_id())
        realtime.emit_project_patch(pid, {"tracks": after_pj["tracks"]}, C.client_id())

        # 转码策略由项目设置决定，默认 off（用源文件，不自动转码）
        _pm = str((ws.project.get("settings") or {}).get("proxy_mode") or "off")
        if _pm == "always" or (_pm == "auto" and not probe.get("browser_ok", True)):
            import threading
            def _warm(src=path):
                try:
                    res = __import__("core.media", fromlist=["ensure_proxy"]).ensure_proxy(src, False)
                    realtime.broadcast_agent_event("proxy.done", {"path": src, "result": res})
                except Exception:
                    pass
            threading.Thread(target=_warm, daemon=True).start()

        return C.ok({"clips": made, "project": after_pj})


# __EOF__