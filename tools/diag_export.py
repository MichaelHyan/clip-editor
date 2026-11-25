# -*- coding: utf-8 -*-
"""本地构建滤镜链，捕获完整 traceback。"""
import sys
import traceback

sys.path.insert(0, "D:/git/clip-editor")

from core import export as ex
from core import store

pid = "proj_00c13582b005"
pj = store.load_project(pid)
clips = store.load_all_clips(pid)
print("项目:", pj.get("name"))
print("片段:", list(clips.keys()))

g = ex.Graph(pj, clips, {"dynamic": "expr"})
try:
    built = g.build()
    print("=== build OK ===")
    print("inputs:", len(built["inputs"]))
    print("filter:")
    print(built["filter"])
    print("warnings:", built["warnings"])
except Exception:
    print("=== build FAILED ===")
    traceback.print_exc()

# __EOF__