# -*- coding: utf-8 -*-
"""验证 split_av：音视频起点一致 + 有冲突时同步后移 + 都落盘正确。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5173"
SRC = "D:/git/clip-editor/data/test_media/sample.mp4"
FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


def main():
    pj, _ = http("POST", "/api/projects", {"name": "分离同步", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    proj, _ = http("GET", "/api/projects/" + pid)
    tracks = proj["data"]["project"]["tracks"]
    vtr = next(t for t in tracks if t["type"] == "video")
    atr = next(t for t in tracks if t["type"] == "audio")

    print("=== 1. 空轨分离：起点应一致，且等于请求值 ===")
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 3.0, "split_av": True})
    cl = d["data"]["clips"]
    chk("生成 2 个", len(cl) == 2, len(cl))
    v = next(c for c in cl if c["media"]["kind"] == "video")
    a = next(c for c in cl if c["media"]["kind"] == "audio")
    chk("起点一致", abs(v["timeline"]["start"] - a["timeline"]["start"]) < 1e-6,
        (v["timeline"]["start"], a["timeline"]["start"]))
    chk("起点 = 3.0", abs(v["timeline"]["start"] - 3.0) < 1e-6, v["timeline"]["start"])

    print("=== 2. 磁盘落盘与内存一致 ===")
    d1, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, v["id"]))
    d2, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, a["id"]))
    chk("视频落盘起点一致",
        abs(d1["data"]["timeline"]["start"] - v["timeline"]["start"]) < 1e-9)
    chk("音频落盘起点一致",
        abs(d2["data"]["timeline"]["start"] - a["timeline"]["start"]) < 1e-9)

    print("=== 3. 音频轨已占用时，分离应整体后移且仍同步 ===")
    # 再分离一次到同一位置（3.0），视频轨和音频轨都已被占 3.0~13.0
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 3.0, "split_av": True})
    cl2 = d["data"]["clips"]
    v2 = next(c for c in cl2 if c["media"]["kind"] == "video")
    a2 = next(c for c in cl2 if c["media"]["kind"] == "audio")
    print("  视频起点:", v2["timeline"]["start"], "音频起点:", a2["timeline"]["start"])
    chk("冲突时音视频仍同步",
        abs(v2["timeline"]["start"] - a2["timeline"]["start"]) < 1e-6,
        (v2["timeline"]["start"], a2["timeline"]["start"]))
    chk("未被放到 3.0（产生了后移）",
        abs(v2["timeline"]["start"] - 3.0) > 1.0, v2["timeline"]["start"])

    print("=== 4. 全项目同轨无重叠 ===")
    proj2, _ = http("GET", "/api/projects/" + pid)
    clips = proj2["data"]["clips"]
    for tinfo in proj2["data"]["project"]["tracks"]:
        ranges = []
        for cid in tinfo["clips"]:
            c = clips.get(cid)
            if not c:
                continue
            st = c["timeline"]["start"] or 0
            en = st + (c["timeline"]["duration"] or 0)
            ranges.append((st, en, c["media"]["kind"]))
        ranges.sort()
        overlapped = False
        for i in range(len(ranges) - 1):
            if ranges[i][1] > ranges[i + 1][0] + 1e-3:
                overlapped = True
                print("  冲突@%s: %s %s" % (tinfo["name"], ranges[i], ranges[i + 1]))
        chk("轨道 %s 无重叠" % tinfo["name"], not overlapped, ranges)

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("SPLIT SYNC ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__