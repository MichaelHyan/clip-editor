# -*- coding: utf-8 -*-
"""清理一次性补丁脚本，保留可复用工具。"""
import os

BASE = "D:/clip-editor"
BASE = "D:/git/clip-editor"
TOOLS = os.path.join(BASE, "tools")

# 保留：启动、统计、验证、浏览器测试、诊断
KEEP_PREFIX = ("launch", "stat", "verify_", "browser_", "diag_", "cleanup_")

removed = []
kept = []
for f in sorted(os.listdir(TOOLS)):
    if not f.endswith(".py"):
        kept.append(f)
        continue
    if f.startswith(KEEP_PREFIX):
        kept.append(f)
    else:
        try:
            os.remove(os.path.join(TOOLS, f))
            removed.append(f)
        except OSError:
            kept.append(f)

print("removed %d patch scripts" % len(removed))
print("kept %d tools:" % len(kept))
for f in kept:
    print("  " + f)

# __EOF__