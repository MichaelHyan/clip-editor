# -*- coding: utf-8 -*-
"""媒体探测 / 浏览 / 缩略图 / 波形 / 代理 / 流式播放。"""
import mimetypes
import os
import threading
from typing import Any

from flask import Blueprint, Response, send_file

from core import config as cfg
from core import media as media_mod
from core import realtime

from . import common as C

bp = Blueprint("media", __name__)


def list_drives():
    """枚举当前可访问的盘符根。"""
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        p = letter + ":/"
        try:
            if os.path.isdir(p):
                out.append(p)
        except OSError:
            pass
    return out


def browse_roots():
    """信任模式下列出盘符 + 用户目录；严格模式下用白名单。"""
    if bool(cfg.get("media_trust_all", True)):
        roots = list_drives()
        home = os.path.expanduser("~").replace("\\", "/")
        if home and os.path.isdir(home) and home not in roots:
            roots.insert(0, home)
        return roots
    return cfg.media_roots()


@bp.route("/playable", methods=["GET"])
@C.wrap
def playable() -> Any:
    """前端用来判断该源是否需要代理转码。"""
    path = C.check_path(C.arg("path"))
    info = media_mod.probe(path)
    st = media_mod.proxy_state(path)
    return C.ok({
        "path": path,
        "kind": info.get("kind"),
        "browser_ok": bool(info.get("browser_ok", True)),
        "issues": info.get("browser_issues") or [],
        "video_codec": info.get("video_codec"),
        "audio_codec": info.get("audio_codec"),
        "proxy_ready": bool(st.get("ready")),
        "proxy_path": st.get("path"),
        "needs_proxy": not bool(info.get("browser_ok", True)),
    })


def _check_dir_allowed(path: str) -> bool:
    if bool(cfg.get("media_trust_all", True)):
        return True
    try:
        real = os.path.realpath(path).replace("\\", "/").lower()
    except OSError:
        return False
    for r in cfg.media_roots():
        rr = os.path.realpath(r).replace("\\", "/").lower()
        if not rr.endswith("/"):
            rr = rr + "/"
        if real == rr.rstrip("/") or real.startswith(rr):
            return True
    return False


@bp.route("/probe", methods=["GET"])
@C.wrap
def probe() -> Any:
    path = C.check_path(C.arg("path"))
    return C.ok(media_mod.probe(path, use_cache=(C.arg("nocache") is None)))


@bp.route("/browse", methods=["GET"])
@C.wrap
def browse() -> Any:
    path = C.arg("path")
    if not path:
        roots = browse_roots()
        return C.ok({"path": None, "parent": None, "roots": roots,
                     "items": [{"name": r, "path": r, "type": "dir", "kind": "dir"}
                               for r in roots]})
    p = str(path).replace("\\", "/")
    if not _check_dir_allowed(p):
        return C.err("目录不在允许范围: " + p, 403)
    return C.ok(media_mod.scan_dir(p, C.argi("limit", 500)))


@bp.route("/thumbnail", methods=["GET"])
@C.wrap
def thumbnail() -> Any:
    path = C.check_path(C.arg("path"))
    data = media_mod.thumbnail_bytes(path, C.argf("t", 0.0), C.argi("w", 320))
    if not data:
        return C.err("缩略图生成失败", 404)
    return Response(data, mimetype="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


@bp.route("/waveform", methods=["GET"])
@C.wrap
def waveform() -> Any:
    path = C.check_path(C.arg("path"))
    peaks = media_mod.waveform(path, C.argi("buckets", 1200))
    return C.ok({"peaks": peaks, "count": len(peaks)})


@bp.route("/proxy", methods=["GET"])
@C.wrap
def proxy_state() -> Any:
    path = C.check_path(C.arg("path"))
    st = media_mod.proxy_state(path)
    st["original"] = path
    return C.ok(st)


@bp.route("/proxy", methods=["POST"])
@C.wrap
def proxy_build() -> Any:
    path = C.check_path(C.arg("path"))
    force = C.arg("force") in ("1", "true", "yes")
    if C.arg("async") in ("1", "true", "yes"):
        def worker() -> None:
            res = media_mod.ensure_proxy(path, force)
            realtime.broadcast_agent_event("proxy.done", {"path": path, "result": res})
        threading.Thread(target=worker, daemon=True).start()
        return C.ok({"status": "queued", "path": path})
    return C.ok(media_mod.ensure_proxy(path, force))


@bp.route("/stream", methods=["GET"])
@C.wrap
def stream() -> Any:
    path = C.check_path(C.arg("path"))
    if not os.path.isfile(path):
        return C.err("文件不存在", 404)
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "application/octet-stream"
    target = path
    if C.arg("proxy") in ("1", "true", "yes"):
        st = media_mod.proxy_state(path)
        if st["ready"]:
            target = st["path"]
            ext = os.path.splitext(target)[1].lower()
            mime = "audio/mp4" if ext in (".m4a", ".aac") else "video/mp4"
    resp = send_file(target, mimetype=mime, conditional=True,
                     as_attachment=False, download_name=os.path.basename(target))
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/config", methods=["GET"])
@C.wrap
def media_config() -> Any:
    return C.ok({
        "media_roots": cfg.media_roots(),
        "proxy": cfg.get("proxy", {}),
        "default_project": cfg.get("default_project", {}),
    })


# __EOF__