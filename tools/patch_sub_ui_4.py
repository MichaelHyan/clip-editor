# -*- coding: utf-8 -*-
"""接线 settings_ui 到 main.js + SRT 字幕导出接口。"""
import io

BASE = "D:/git/clip-editor"


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def sub(s, old, new, label):
    if old not in s:
        if new in s:
            print("  [skip] %s" % label)
            return s
        raise SystemExit("  [FAIL] 锚点缺失: %s" % label)
    print("  [ok]   %s" % label)
    return s.replace(old, new, 1)


# ================================================================ main.js
p = BASE + "/static/js/main.js"
s = read(p)

s = sub(s,
        'import * as projectsUi from "./projects_ui.js";',
        'import * as projectsUi from "./projects_ui.js";\n'
        'import * as settingsUi from "./settings_ui.js";',
        "import settingsUi")

s = sub(s,
        "  projectsUi.init();",
        "  projectsUi.init();\n  settingsUi.init();",
        "init settingsUi")

s = sub(s,
        '''  S.bus.on("project:changed", () => updateHeader());''',
        '''  S.bus.on("project:changed", () => {
    updateHeader();
    fitPreview();
  });''',
        "project:changed 重算预览尺寸")

write(p, s)

# ================================================================ SRT 导出
p = BASE + "/api/routes_export.py"
s = read(p)

s = sub(s,
        '''@bp.route("/preview-cmd/<pid>", methods=["GET"])''',
        '''@bp.route("/subtitles/<pid>/srt", methods=["GET"])
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
    body = "\\r\\n".join(lines)
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="subtitles.srt"'})


@bp.route("/preview-cmd/<pid>", methods=["GET"])''',
        "SRT 导出接口")

write(p, s)
print("done")

# __EOF__