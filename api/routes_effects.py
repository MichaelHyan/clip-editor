# -*- coding: utf-8 -*-
"""效果注册表接口（agent 动态注册入口）。"""
from typing import Any

from flask import Blueprint

from core import effects as effects_mod
from core import realtime

from . import common as C

bp = Blueprint("effects", __name__)


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@C.wrap
def list_effects() -> Any:
    scope = C.arg("scope")
    inc = C.arg("include_disabled") in ("1", "true", "yes")
    return C.ok({
        "registry": effects_mod.snapshot(),
        "list": effects_mod.list_effects(scope, inc),
    })


@bp.route("/<etype>", methods=["GET"])
@C.wrap
def get_effect(etype: str) -> Any:
    e = effects_mod.get_effect(etype)
    if not e:
        return C.err("效果不存在: " + etype, 404)
    return C.ok(e)


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
@C.wrap
def register_effect() -> Any:
    b = C.body()
    etype = b.get("type") or b.get("id")
    definition = b.get("definition") or {k: v for k, v in b.items()
                                         if k not in ("type", "id", "definition")}
    if not etype:
        return C.err("缺少效果 ID（type）")
    d = effects_mod.register(str(etype), definition)
    realtime.emit_effects(d, "registered")
    realtime.broadcast_agent_event("effect.registered",
                                   {"type": d["type"], "effect": d})
    return C.ok(d)


@bp.route("/<etype>", methods=["PATCH"])
@C.wrap
def update_effect(etype: str) -> Any:
    b = C.body()
    d = effects_mod.update(etype, b.get("definition") or b)
    realtime.emit_effects(d, "updated")
    return C.ok(d)


@bp.route("/<etype>", methods=["DELETE"])
@C.wrap
def delete_effect(etype: str) -> Any:
    ok = effects_mod.unregister(etype)
    if not ok:
        return C.err("效果不存在: " + etype, 404)
    realtime.emit_effects({"type": etype}, "unregistered")
    return C.ok({"deleted": etype})


@bp.route("/reload", methods=["POST"])
@C.wrap
def reload_registry() -> Any:
    effects_mod.reload_registry()
    return C.ok(effects_mod.snapshot())


# __EOF__