# -*- coding: utf-8 -*-
import os

BASE = "D:/git/clip-editor"


def count(rel, ext):
    n = 0
    lines = 0
    for r, _, fs in os.walk(os.path.join(BASE, rel)):
        if "__pycache__" in r:
            continue
        for f in fs:
            if f.endswith(ext):
                n += 1
                try:
                    with open(os.path.join(r, f), encoding="utf-8", errors="replace") as fh:
                        lines += len(fh.read().splitlines())
                except OSError:
                    pass
    return n, lines


rows = [
    ("core", ".py"),
    ("api", ".py"),
    ("static/js", ".js"),
    ("static/css", ".css"),
    ("tools", ".py"),
]
total_f = total_l = 0
for rel, ext in rows:
    n, l = count(rel, ext)
    total_f += n
    total_l += l
    print("%-12s %3d files  %6d lines" % (rel, n, l))
print("%-12s %3d files  %6d lines" % ("TOTAL", total_f, total_l))

# 根目录文件
for f in sorted(os.listdir(BASE)):
    p = os.path.join(BASE, f)
    if os.path.isfile(p) and f.endswith((".py", ".md", ".json")):
        with open(p, encoding="utf-8", errors="replace") as fh:
            print("%-12s   -        %6d lines" % (f, len(fh.read().splitlines())))

# __EOF__