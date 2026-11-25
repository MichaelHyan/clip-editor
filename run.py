# -*- coding: utf-8 -*-
"""clip-editor 启动入口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, render_template, request  # noqa: E402

from api import register as register_api  # noqa: E402
from core import config as cfg  # noqa: E402
from core import effects, media, realtime  # noqa: E402

app = Flask(__name__, static_folder="static", template_folder="templates")
app.json.ensure_ascii = False
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024
# debug=False 时 Flask 会缓存模板，改 HTML 不生效。强制每次重新读取。
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

register_api(app)

try:
    realtime.sio.init_app(app)
except Exception as exc:
    print("[warn] SocketIO 初始化失败:", exc, file=sys.stderr)

effects.on_change(lambda snap: realtime.emit_effects(snap))


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "version": "0.1.0",
        "phase": "P1-backend",
        "ffmpeg": media.FFMPEG,
        "ffprobe": media.FFPROBE,
        "effects": len(effects.list_effects()),
        "media_roots": cfg.media_roots(),
        "default_project": cfg.get("default_project", {}),
        "socketio": bool(getattr(realtime, "_HAS_SIO", False)),
    })


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "接口不存在: " + request.path}), 404
    return render_template("index.html"), 200


def main() -> None:
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 5173))
    debug = bool(cfg.get("debug", False))
    print("=" * 56)
    print(" clip-editor  P1")
    print("   http://%s:%d" % (host, port))
    print("   ffmpeg:", media.FFMPEG)
    print("=" * 56)
    try:
        realtime.sio.run(app, host=host, port=port, debug=debug,
                         allow_unsafe_werkzeug=True)
    except Exception as exc:
        print("[warn] socketio.run 失败，回退 app.run:", exc, file=sys.stderr)
        app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()


# __EOF__