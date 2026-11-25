# -*- coding: utf-8 -*-
"""导出接口：启动 / 查询 / 列表 / 取消 / 删除 / 预览命令。"""
import os
from typing import Any

from flask import Blueprint, send_file

from core import export as export_mod
from core import workspace

from . import common as C

bp = Blueprint("export", __name__)


def _opts_from(b: dict) -> dict:
    opts = {
        "width": b.get("width"),
        "height": b.get("height"),
        "fps": b.get("fps"),
        "sample_rate": b.get("sample_rate"),
        "dynamic": b.get("dynamic") or "expr",
        "filename": b.get("filename") or "output",
        "preset": b.get("preset"),
        "crf": b.get("crf", 20),
        "abitrate": b.get("abitrate"),
        "vbitrate": b.get("vbitrate"),
        "pix_fmt": b.get("pix_fmt"),
    }
    return {k: v for k, v in opts.items() if v is not None}


@bp.route("/presets", methods=["GET"])
@C.wrap
def presets() -> Any:
    return C.ok({"presets": export_mod.PRESETS})


@bp.route("/<pid>", methods=["POST"])
@C.wrap
def start(pid: str) -> Any:
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    b = C.body()
    opts = _opts_from(b)
    with ws.lock:
        job = export_mod.start_export(ws.project, dict(ws.clips), opts)
    return C.ok(job)


@bp.route("/jobs", methods=["GET"])
@C.wrap
def list_jobs() -> Any:
    pid = C.arg("project_id")
    return C.ok({"jobs": export_mod.list_jobs(pid)})


@bp.route("/jobs/<jid>", methods=["GET"])
@C.wrap
def get_job(jid: str) -> Any:
    job = export_mod.get_job(jid)
    if not job:
        return C.err("任务不存在: " + jid, 404)
    return C.ok(job)


@bp.route("/jobs/<jid>/cancel", methods=["POST"])
@C.wrap
def cancel(jid: str) -> Any:
    ok = export_mod.cancel_job(jid)
    if not ok:
        return C.err("任务无法取消（可能已完成或不存在）", 400)
    return C.ok({"cancelled": jid})


@bp.route("/jobs/<jid>", methods=["DELETE"])
@C.wrap
def delete(jid: str) -> Any:
    ok = export_mod.delete_export(jid)
    if not ok:
        return C.err("任务不存在", 404)
    return C.ok({"deleted": jid})


@bp.route("/jobs/<jid>/download", methods=["GET"])
@C.wrap
def download(jid: str) -> Any:
    job = export_mod.get_job(jid)
    if not job:
        return C.err("任务不存在", 404)
    if job.get("status") != "done":
        return C.err("任务未完成", 400)
    path = job.get("output")
    if not path or not os.path.isfile(path):
        return C.err("输出文件不存在", 404)
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


@bp.route("/subtitles/<pid>/srt", methods=["GET"])
@C.wrap
def export_srt(pid: str) -> Any:
    """导出 SRT 字幕文件。"""
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    subs = ws.project.get("subtitles") or []
    if not subs:
        return C.err("项目中没有字幕", 400)

    def fmt(t):
        t = max(0.0, float(t))
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        sec = int(t % 60)
        ms = int(round((t - int(t)) * 1000))
        if ms >= 1000:
            ms = 999
        return "%02d:%02d:%02d,%03d" % (h, m, sec, ms)

    lines = []
    ordered = sorted(subs, key=lambda x: x.get("start", 0))
    for i, item in enumerate(ordered, 1):
        lines.append(str(i))
        lines.append("%s --> %s" % (fmt(item.get("start", 0)),
                                    fmt(item.get("end", 0))))
        lines.append(str(item.get("text") or "").strip() or " ")
        lines.append("")

    from flask import Response
    body = "\r\n".join(lines)
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="subtitles.srt"'})


@bp.route("/preview-cmd/<pid>", methods=["GET"])
@C.wrap
def preview_cmd(pid: str) -> Any:
    """只生成命令与告警，不执行，供排查滤镜链。"""
    ws, e = C.ws_or_404(pid)
    if e:
        return e
    opts = _opts_from({
        "dynamic": C.arg("dynamic", "expr"),
        "width": C.arg("width"),
        "height": C.arg("height"),
        "fps": C.arg("fps"),
    })
    with ws.lock:
        g = export_mod.Graph(ws.project, dict(ws.clips), opts)
        built = g.build()
        cmd = g.command("OUTPUT.mp4", {
            "vcodec": "libx264", "preset": "medium", "crf": 20,
            "pix_fmt": "yuv420p", "abitrate": "192k",
        })
    return C.ok({
        "command": cmd,
        "filter": built["filter"],
        "inputs": built["inputs"],
        "warnings": built["warnings"],
        "total_duration": built["total_duration"],
    })


# __EOF__