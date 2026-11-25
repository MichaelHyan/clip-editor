# -*- coding: utf-8 -*-
"""Agent 工具接口层。

agent 直接写入工具定义 -> 立即出现在前端效果器面板，无需重启、无需改前端。
与 /api/effects 共享同一注册表（core.effects）；这里是面向 agent 的语义化封装：
概念上把 effect 称为 tool（工具），实例称为 tool instance。
"""
import copy
import time
from typing import Any, Dict

from flask import Blueprint

from core import clip as clip_mod
from core import effects as effects_mod
from core import keyframe as kfmod
from core import realtime, workspace

from . import common as C

bp = Blueprint("agent", __name__)


def _tool_view(e: Dict[str, Any]) -> Dict[str, Any]:
    """把内部 effect 定义转成 agent 视角的工具描述。"""
    return {
        "id": e.get("type") or e.get("id"),
        "name": e.get("name") or e.get("type"),
        "category": e.get("category", "custom"),
        "scope": e.get("scope", "video"),
        "params": e.get("params") or {},
        "preview": e.get("preview") or {},
        "export": e.get("ffmpeg") or e.get("export") or {},
        "ui": e.get("ui") or {},
        "action": e.get("action"),
        "builtin": bool(e.get("builtin")),
        "source": e.get("source", "agent"),
        "disabled": bool(e.get("disabled")),
    }


def _normalize_defn(b: Dict[str, Any]) -> Dict[str, Any]:
    """接受扁平写法或 {definition:{...}} 两种写法。"""
    if isinstance(b.get("definition"), dict):
        d = dict(b["definition"])
    else:
        d = {k: v for k, v in b.items() if k not in ("id", "type", "project_id")}
    if "export" in d and "ffmpeg" not in d:
        d["ffmpeg"] = d["export"]
    d.pop("id", None)
    d.pop("type", None)
    return d


def _state(cid: str, clip: Any) -> Dict[str, Any]:
    return {"target": "clip", "id": cid, "state": copy.deepcopy(clip)}


def _find_instance(clip: Dict[str, Any], iid: str) -> Any:
    for eff in clip.get("effects", []):
        if eff.get("id") == iid:
            return eff
    return None


# ---------------------------------------------------------------- 工具注册表

@bp.route("/tools", methods=["GET"])
@C.wrap
def list_tools() -> Any:
    scope = C.arg("scope")
    inc = C.arg("include_disabled") in ("1", "true", "yes")
    items = [_tool_view(e) for e in effects_mod.list_effects(scope, inc)]
    return C.ok({"tools": items, "count": len(items),
                 "version": effects_mod.snapshot().get("version", 1)})


@bp.route("/tools", methods=["POST"])
@C.wrap
def register_tool() -> Any:
    b = C.body()
    tid = b.get("id") or b.get("type")
    if not tid:
        return C.err("缺少工具 id")
    tid = str(tid).strip()
    defn = _normalize_defn(b)
    existing = effects_mod.get_effect(tid)
    if existing and existing.get("builtin"):
        return C.err("内置工具不可覆盖，请用新 id", 403)
    try:
        if existing and existing.get("source") == "agent":
            d = effects_mod.update(tid, defn)
            action = "updated"
        else:
            d = effects_mod.register(tid, defn)
            action = "registered"
    except ValueError as exc:
        return C.err(str(exc), 400)
    except KeyError as exc:
        return C.err("工具不存在: " + str(exc), 404)
    realtime.emit_effects(d, action)
    realtime.broadcast_agent_event("tool." + action,
                                   {"id": d.get("type"), "tool": _tool_view(d)})
    return C.ok(_tool_view(d))


@bp.route("/tools/<tid>", methods=["GET"])
@C.wrap
def get_tool(tid: str) -> Any:
    e = effects_mod.get_effect(tid)
    if not e:
        return C.err("工具不存在: " + tid, 404)
    return C.ok(_tool_view(e))


@bp.route("/tools/<tid>", methods=["DELETE"])
@C.wrap
def delete_tool(tid: str) -> Any:
    e = effects_mod.get_effect(tid)
    if e and e.get("builtin"):
        return C.err("内置工具不可删除", 403)
    ok = effects_mod.unregister(tid)
    if not ok:
        return C.err("工具不存在: " + tid, 404)
    realtime.emit_effects({"type": tid}, "unregistered")
    realtime.broadcast_agent_event("tool.unregistered", {"id": tid})
    return C.ok({"deleted": tid})


# ---------------------------------------------------------------- 项目上下文

@bp.route("/context", methods=["GET"])
@C.wrap
def context() -> Any:
    pid = C.arg("project_id")
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        durs = ws.durations()
        clips_summary = []
        for cid, c in ws.clips.items():
            clips_summary.append({
                "id": cid,
                "name": c.get("name"),
                "kind": (c.get("media") or {}).get("kind"),
                "media": (c.get("media") or {}).get("path"),
                "track_id": (c.get("timeline") or {}).get("track_id"),
                "start": (c.get("timeline") or {}).get("start"),
                "duration": durs.get(cid),
                "rev": c.get("rev", 1),
                "tools": [{"instance": fx.get("id"), "type": fx.get("type"),
                           "enabled": fx.get("enabled", True)}
                          for fx in c.get("effects", [])],
                "markers": c.get("markers", []),
            })
        clips_summary.sort(key=lambda x: (x.get("start") or 0.0))
        tl = ws.timeline()
        return C.ok({
            "project": {
                "id": pid,
                "name": ws.project.get("name"),
                "settings": ws.project.get("settings"),
                "playhead": ws.project.get("playhead", 0.0),
                "rev": ws.project.get("rev", 1),
            },
            "tracks": ws.project.get("tracks", []),
            "clips": clips_summary,
            "markers": ws.project.get("markers", []),
            "total_duration": tl.get("total_duration", 0.0),
            "history": tl.get("history", {}),
        })


@bp.route("/clip/<cid>", methods=["GET"])
@C.wrap
def clip_info(cid: str) -> Any:
    pid = C.arg("project_id")
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        c = ws.clips.get(cid)
        if c is None:
            return C.err("片段不存在", 404)
        return C.ok({
            "clip": c,
            "duration": ws.durations().get(cid),
            "resolve_at_0": clip_mod.resolve_at(c, 0.0),
        })


# ---------------------------------------------------------------- 应用工具到片段

@bp.route("/clip/<cid>/apply-tool", methods=["POST"])
@C.wrap
def apply_tool(cid: str) -> Any:
    b = C.body()
    pid = b.get("project_id") or C.arg("project_id")
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    tid = b.get("tool_id") or b.get("type")
    if not tid:
        return C.err("缺少 tool_id")
    if not effects_mod.get_effect(tid):
        return C.err("工具不存在: " + str(tid), 404)
    with ws.lock:
        c = ws.clips.get(cid)
        if c is None:
            return C.err("片段不存在", 404)
        eff = effects_mod.instantiate(tid, b.get("instance_id"), b.get("params"))
        if b.get("enabled") is False:
            eff["enabled"] = False
        before = copy.deepcopy(c)
        c.setdefault("effects", []).append(eff)
        c["rev"] = int(c.get("rev", 1)) + 1
        c["meta"]["updated"] = time.time()
        c = clip_mod.normalize(c)
        ws.save_clip(c)
        ws.commit("agent 应用工具 " + str(eff.get("meta", {}).get("name")),
                  [_state(cid, before)], [_state(cid, c)], C.client_id())
        realtime.emit_clip(pid, c, "update", C.client_id())
        return C.ok({"instance": eff, "clip": c})


@bp.route("/clip/<cid>/tool/<iid>", methods=["PATCH"])
@C.wrap
def patch_tool(cid: str, iid: str) -> Any:
    b = C.body()
    pid = b.get("project_id") or C.arg("project_id")
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        c = ws.clips.get(cid)
        if c is None:
            return C.err("片段不存在", 404)
        inst = _find_instance(c, iid)
        if inst is None:
            return C.err("工具实例不存在: " + iid, 404)
        before = copy.deepcopy(c)
        if "enabled" in b:
            inst["enabled"] = bool(b["enabled"])
        params = b.get("params")
        if isinstance(params, dict):
            inst.setdefault("params", {})
            for pname, pval in params.items():
                if isinstance(pval, dict) and ("static" in pval or "keys" in pval):
                    inst["params"][pname] = pval
                else:
                    inst["params"][pname] = {"static": pval}
        for kfitem in (b.get("keyframes") or []):
            path = kfitem.get("path")
            if not path:
                continue
            inst.setdefault("params", {})
            inst["params"][path] = kfmod.set_key(
                inst["params"].get(path), float(kfitem.get("t", 0.0)),
                kfitem.get("value"), kfitem.get("e", "linear"), kfitem.get("h"))
        c["rev"] = int(c.get("rev", 1)) + 1
        c["meta"]["updated"] = time.time()
        c = clip_mod.normalize(c)
        ws.save_clip(c)
        ws.commit("agent 修改工具参数 " + iid,
                  [_state(cid, before)], [_state(cid, c)], C.client_id())
        realtime.emit_clip(pid, c, "update", C.client_id())
        return C.ok(c)


@bp.route("/clip/<cid>/tool/<iid>", methods=["DELETE"])
@C.wrap
def remove_tool(cid: str, iid: str) -> Any:
    b = C.body()
    pid = b.get("project_id") or C.arg("project_id")
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    with ws.lock:
        c = ws.clips.get(cid)
        if c is None:
            return C.err("片段不存在", 404)
        inst = _find_instance(c, iid)
        if inst is None:
            return C.err("工具实例不存在: " + iid, 404)
        before = copy.deepcopy(c)
        c["effects"] = [x for x in c.get("effects", []) if x.get("id") != iid]
        c["rev"] = int(c.get("rev", 1)) + 1
        c = clip_mod.normalize(c)
        ws.save_clip(c)
        ws.commit("agent 移除工具 " + iid,
                  [_state(cid, before)], [_state(cid, c)], C.client_id())
        realtime.emit_clip(pid, c, "update", C.client_id())
        return C.ok(c)


# __EOF__