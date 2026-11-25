# -*- coding: utf-8 -*-
"""后端：项目设置校验 + 字幕数据模型与 CRUD。

1. project.py: 轨道类型加 subtitle；默认轨道 FX1 -> SUB1
2. project.py: subtitles[] 字段 + 增删改函数
3. routes_project.py: 字幕 CRUD 接口 + settings 范围校验
"""
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


# ================================================================ project.py
p = BASE + "/core/project.py"
s = read(p)

s = sub(s,
        'TRACK_TYPES = ("video", "audio", "effect", "marker")',
        'TRACK_TYPES = ("video", "audio", "effect", "marker", "subtitle")',
        "TRACK_TYPES 加 subtitle")

s = sub(s,
        '''    tracks = [
        make_track("video", "V2", 0),
        make_track("video", "V1", 1),
        make_track("effect", "FX1", 2),
        make_track("audio", "A1", 3),
    ]''',
        '''    tracks = [
        make_track("video", "V2", 0),
        make_track("video", "V1", 1),
        make_track("subtitle", "SUB1", 2),
        make_track("audio", "A1", 3),
    ]''',
        "默认轨道 FX1 -> SUB1")

s = sub(s,
        '''        "tracks": tracks,
        "markers": [],''',
        '''        "tracks": tracks,
        "markers": [],
        "subtitles": [],''',
        "make_project 加 subtitles")

s = sub(s,
        '''    p.setdefault("tracks", [])
    p.setdefault("markers", [])''',
        '''    p.setdefault("tracks", [])
    p.setdefault("markers", [])
    p.setdefault("subtitles", [])''',
        "normalize 加 subtitles")

s = sub(s,
        '''def total_duration(project: Dict[str, Any], durations: Dict[str, float]) -> float:''',
        '''# ---------------------------------------------------------------- 字幕

SUB_STYLE_DEFAULT = {
    "fontSize": 48,
    "color": "#ffffff",
    "outlineColor": "#000000",
    "outline": 3,
    "x": 0.5,
    "y": 0.86,
    "align": "center",
    "bold": True,
}


def _norm_sub_style(style: Any) -> Dict[str, Any]:
    out = dict(SUB_STYLE_DEFAULT)
    if isinstance(style, dict):
        for k, v in style.items():
            if k in out:
                out[k] = v
    try:
        out["fontSize"] = max(8, min(400, int(out["fontSize"])))
    except (TypeError, ValueError):
        out["fontSize"] = 48
    try:
        out["x"] = max(0.0, min(1.0, float(out["x"])))
        out["y"] = max(0.0, min(1.0, float(out["y"])))
    except (TypeError, ValueError):
        out["x"], out["y"] = 0.5, 0.86
    if out["align"] not in ("left", "center", "right"):
        out["align"] = "center"
    return out


def add_subtitle(project: Dict[str, Any], start: float, end: float, text: str,
                 style: Any = None) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p.setdefault("subtitles", [])
    st = max(0.0, float(start))
    en = max(st + 0.05, float(end))
    p["subtitles"].append({
        "id": f"sub_{uuid.uuid4().hex[:10]}",
        "start": st,
        "end": en,
        "text": str(text or ""),
        "style": _norm_sub_style(style),
    })
    p["subtitles"].sort(key=lambda x: x["start"])
    p["meta"]["updated"] = time.time()
    return p


def update_subtitle(project: Dict[str, Any], sid: str,
                    patch: Dict[str, Any]) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    for item in p.get("subtitles") or []:
        if item.get("id") != sid:
            continue
        if "start" in patch:
            item["start"] = max(0.0, float(patch["start"]))
        if "end" in patch:
            item["end"] = max(item["start"] + 0.05, float(patch["end"]))
        if "text" in patch:
            item["text"] = str(patch["text"] or "")
        if "style" in patch and isinstance(patch["style"], dict):
            merged = dict(item.get("style") or {})
            merged.update(patch["style"])
            item["style"] = _norm_sub_style(merged)
        break
    p["subtitles"].sort(key=lambda x: x["start"])
    p["meta"]["updated"] = time.time()
    return p


def remove_subtitle(project: Dict[str, Any], sid: str) -> Dict[str, Any]:
    p = copy.deepcopy(project)
    p["subtitles"] = [x for x in (p.get("subtitles") or [])
                      if x.get("id") != sid]
    p["meta"]["updated"] = time.time()
    return p


def find_subtitle(project: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    for x in project.get("subtitles") or []:
        if x.get("id") == sid:
            return x
    return None


def total_duration(project: Dict[str, Any], durations: Dict[str, float]) -> float:''',
        "字幕增删改函数")

write(p, s)

# ================================================================ routes_project.py
p = BASE + "/api/routes_project.py"
s = read(p)

s = sub(s,
        '''@bp.route("/<pid>", methods=["PATCH"])
@C.wrap
def patch_project(pid: str) -> Any:''',
        '''_SETTINGS_LIMITS = {
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
def patch_project(pid: str) -> Any:''',
        "settings 校验函数")

s = sub(s,
        '''    patch: Dict[str, Any] = b.get("patch") or {}
    with ws.lock:
        before = copy.deepcopy(ws.project)
        pj = copy.deepcopy(ws.project)''',
        '''    patch: Dict[str, Any] = b.get("patch") or {}
    patch = _validate_settings(dict(patch))
    with ws.lock:
        before = copy.deepcopy(ws.project)
        pj = copy.deepcopy(ws.project)''',
        "patch_project 调用校验")

s = sub(s,
        '''@bp.route("/<pid>/snapshot", methods=["POST"])''',
        '''# ---------------------------------------------------------------- 字幕

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


@bp.route("/<pid>/snapshot", methods=["POST"])''',
        "字幕 CRUD 接口")

write(p, s)
print("done")

# __EOF__