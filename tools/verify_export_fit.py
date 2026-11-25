# -*- coding: utf-8 -*-
"""验证导出：源铺满画布 + 擦除生效 + 滤镜链不重复。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5173"
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
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


def wait_job(jid, timeout=300):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        j, _ = http("GET", "/api/export/jobs/" + jid)
        j = j["data"]
        if j["status"] != last:
            last = j["status"]
            print("    %s %.0f%%" % (j["status"], j["progress"] * 100))
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.8)
    return j


def probe_frame(path, t):
    """抽一帧，返回非黑区域包围盒占画布的比例。"""
    out = "D:/git/clip-editor/data/_probe.jpg"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(t), "-i", path, "-frames:v", "1", out],
                   capture_output=True)
    if not os.path.isfile(out):
        return None
    from PIL import Image
    im = Image.open(out).convert("RGB")
    w, h = im.size
    px = im.load()
    minx, maxx, miny, maxy = w, -1, h, -1
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            r, g, b = px[x, y]
            if r > 12 or g > 12 or b > 12:
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    os.remove(out)
    if maxx < 0:
        return {"empty": True}
    return {
        "empty": False,
        "w": maxx - minx, "h": maxy - miny,
        "fill_w": (maxx - minx) / w, "fill_h": (maxy - miny) / h,
        "canvas": [w, h],
    }


def main():
    print("=== 准备：852x480 源放到 1920x1080 项目 ===")
    pj, _ = http("POST", "/api/projects", {"name": "导出尺寸验证",
                                          "settings": {"width": 1920, "height": 1080, "fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid,
                  {"path": "D:/git/clip-editor/data/test_media/sample.mp4"})
    aid = res["data"]["asset"]["id"]
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 0.0})
    cid = d["data"]["clips"][0]["id"]
    print("  pid=%s cid=%s" % (pid, cid))

    print("=== 1. 滤镜链检查 ===")
    pc, _ = http("GET", "/api/export/preview-cmd/" + pid)
    f = pc["data"]["filter"]
    print("  filter 长度:", len(f))
    # [vbase0] 正确出现 2 次：1 次定义 color=...[vbase0]，1 次引用 [vbase0][v0]overlay
    # 重复 bug 时会是 4 次（两套完整链）
    chk("滤镜链无重复（vbase0 恰好 2 次）", f.count("[vbase0]") == 2, f.count("[vbase0]"))
    chk("vout 只定义一次", f.count("[vout]") == 1, f.count("[vout]"))
    chk("有 fit 缩放", "min(1920/iw,1080/ih)" in f, f[:200])
    chk("输入数正确", len(pc["data"]["inputs"]) == 1, len(pc["data"]["inputs"]))

    print("=== 2. 导出并检查画面填充 ===")
    job, _ = http("POST", "/api/export/" + pid,
                  {"filename": "fit_test", "preset": "veryfast", "crf": 28})
    jid = job["data"]["id"]
    j = wait_job(jid)
    chk("导出成功", j["status"] == "done", (j["status"], (j.get("error") or "")[:300]))

    if j["status"] == "done":
        out = j["output"]
        info = probe_frame(out, 3.0)
        print("  画面包围盒:", info)
        chk("画面非空", info and not info.get("empty"), info)
        if info and not info.get("empty"):
            # 852x480 在 1920x1080 里等比铺满 -> 宽占 100%，高占 56.25%
            chk("宽度铺满画布 (>95%)", info["fill_w"] > 0.95, info["fill_w"])
            chk("高度约 56%（16:9 源在 16:9 画布应铺满，此处源同为16:9）",
                info["fill_h"] > 0.9, info["fill_h"])

    print("=== 3. 擦除效果 ===")
    # 设置 erode=0.5，从左侧擦除 50%
    http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
         {"patch": {"video.erode": 0.5, "video.erode_softness": 0.05,
                    "video.erode_dir": "left"}})
    c, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, cid))
    chk("erode 已写入", abs(c["data"]["video"]["erode"]["static"] - 0.5) < 1e-6,
        c["data"]["video"]["erode"])
    chk("erode_dir 已写入", c["data"]["video"]["erode_dir"] == "left",
        c["data"]["video"].get("erode_dir"))

    pc2, _ = http("GET", "/api/export/preview-cmd/" + pid)
    f2 = pc2["data"]["filter"]
    chk("滤镜链含擦除 geq", "geq=" in f2 and "a=" in f2, f2[-400:])

    job2, _ = http("POST", "/api/export/" + pid,
                   {"filename": "erode_test", "preset": "veryfast", "crf": 28})
    j2 = wait_job(job2["data"]["id"])
    chk("擦除导出成功", j2["status"] == "done", (j2["status"], (j2.get("error") or "")[:300]))

    if j2["status"] == "done":
        info2 = probe_frame(j2["output"], 3.0)
        print("  擦除后包围盒:", info2)
        if info and not info.get("empty") and info2 and not info2.get("empty"):
            # 左边被擦掉一半，包围盒宽度应约为原来的一半
            ratio = info2["fill_w"] / max(1e-6, info["fill_w"])
            print("  宽度比例: %.2f（期望 ≈0.5）" % ratio)
            chk("擦除后宽度减半", 0.35 < ratio < 0.65, ratio)

    # 清理
    for jj in (jid, j2.get("id") if j2 else None):
        if jj:
            try:
                http("DELETE", "/api/export/jobs/" + jj)
            except Exception:
                pass
    http("DELETE", "/api/projects/" + pid)

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("EXPORT FIT ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__