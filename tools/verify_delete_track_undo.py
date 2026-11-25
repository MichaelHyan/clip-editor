# -*- coding: utf-8 -*-
"""API 级验证：删除带片段的轨道后，撤销能否恢复片段。

不依赖浏览器。流程：
  1. 建临时项目
  2. 导入一个片段到视频轨
  3. DELETE 该轨道          -> 片段应从 state 消失
  4. POST undo              -> 轨道和片段都应回来，且片段仍挂在该轨道
  5. 删除临时项目
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:5173/api"
SAMPLE = "D:/git/clip-editor/data/test_media/sample.mp4"


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print(("  [OK] " if cond else "  [FAIL] ") + name + (("  -> " + str(detail)) if detail else ""))
        if not cond:
            ok = False

    pid = http("POST", API + "/projects", {"name": "__undo_test__"})["data"]["project"]["id"]
    print("临时项目:", pid)
    try:
        tl = http("GET", API + "/projects/" + pid)["data"]
        vtrack = next(t for t in tl["project"]["tracks"] if t["type"] == "video")

        imported = http("POST", API + "/projects/" + pid + "/clips",
                        {"path": SAMPLE, "track_id": vtrack["id"]})["data"]
        cid = imported["id"]
        chk("片段已导入", bool(cid), cid)

        tl = http("GET", API + "/projects/" + pid)["data"]
        chk("片段在 state 里", cid in tl["clips"])
        tr = next(t for t in tl["project"]["tracks"] if t["id"] == vtrack["id"])
        chk("片段挂在轨道上", cid in tr["clips"], tr["clips"])

        # 删轨道
        http("DELETE", API + "/projects/" + pid + "/tracks/" + vtrack["id"])
        tl = http("GET", API + "/projects/" + pid)["data"]
        chk("删除后轨道不在了",
            all(t["id"] != vtrack["id"] for t in tl["project"]["tracks"]))
        chk("删除后片段也不在了", cid not in tl["clips"])

        # 撤销
        http("POST", API + "/projects/" + pid + "/undo", {})
        tl = http("GET", API + "/projects/" + pid)["data"]

        back_track = next((t for t in tl["project"]["tracks"] if t["id"] == vtrack["id"]), None)
        chk("撤销后轨道回来", back_track is not None)
        chk("撤销后片段内容回来", cid in tl["clips"], cid)
        chk("片段重新挂在轨道上",
            bool(back_track) and cid in (back_track.get("clips") or []),
            back_track.get("clips") if back_track else None)

        # 再撤销一步（撤销导入）应该也能工作
        http("POST", API + "/projects/" + pid + "/undo", {})
        tl = http("GET", API + "/projects/" + pid)["data"]
        chk("继续撤销后片段消失", cid not in tl["clips"])
    finally:
        try:
            http("DELETE", API + "/projects/" + pid)
            print("已删除临时项目:", pid)
        except Exception as e:
            print("清理失败:", e)

    print("\n== %s ==" % ("全部通过" if ok else "存在失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# __EOF__
