# -*- coding: utf-8 -*-
"""验证：同轨防重叠 + 右边缘可拉长 + 素材截取导入。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
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
    print("=== 准备项目 ===")
    pj, _ = http("POST", "/api/projects", {"name": "修复验证", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]

    print("=== 1. 素材截取导入（后端）===")
    d, code = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                   {"role": "video", "start": 0.0, "src_in": 2.0, "src_out": 6.0})
    chk("截取导入成功", code == 200 and d.get("ok"), (code, d.get("error")))
    c = d["data"]["clips"][0]
    chk("src_in = 2", abs(c["video"]["src_in"]["static"] - 2.0) < 1e-6, c["video"]["src_in"])
    chk("src_out = 6", abs(c["video"]["src_out"]["static"] - 6.0) < 1e-6, c["video"]["src_out"])
    chk("时长 = 4s", abs(c["timeline"]["duration"] - 4.0) < 0.1, c["timeline"]["duration"])
    print("  片段时长 %.2fs" % c["timeline"]["duration"])

    print("=== 2. 截取超出源时长会被钳制 ===")
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "audio", "start": 20.0, "src_in": 0, "src_out": 9999})
    c2 = d["data"]["clips"][0]
    chk("src_out 被钳到源时长", c2["audio"]["src_out"]["static"] <= 10.1,
        c2["audio"]["src_out"])

    print("=== 3. 出入点非法时拒绝 ===")
    d, code = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                   {"role": "video", "src_in": 8.0, "src_out": 3.0})
    chk("出点小于入点被拒", code == 400, code)

    print("=== 浏览器验证：重叠与拉长 ===")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))

        # 清掉项目里的片段，重新铺两个便于测重叠
        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.0)

        print("=== 4. 把第一个片段右边缘拉长（应该能拉回 10s）===")
        # 找到视频片段，先缩到 6s，再拉回
        vid = None
        d, _ = http("GET", "/api/projects/%s/clips" % pid)
        for cid, cc in d["data"]["clips"].items():
            if cc["media"]["kind"] == "video":
                vid = cc
                break
        chk("找到视频片段", vid is not None)
        if vid:
            # 先缩到 src_out=5
            http("POST", "/api/projects/%s/clips/%s/trim" % (pid, vid["id"]),
                 {"src_in": None, "src_out": 5.0})
            page.reload(wait_until="networkidle")
            time.sleep(2.0)
            # 拖右边缘
            res = page.evaluate("""(cid) => {
              const node = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
              if (!node) return {err: 'no-node'};
              const r = node.getBoundingClientRect();
              // 记录拖拽前的宽度
              const beforeW = r.width;
              // 模拟拖右边缘 +200px
              const down = new PointerEvent('pointerdown', {
                clientX: r.right - 2, clientY: r.top + r.height/2,
                bubbles: true, button: 0 });
              node.dispatchEvent(down);
              const move = new PointerEvent('pointermove', {
                clientX: r.right + 200, clientY: r.top + r.height/2,
                bubbles: true, button: 0 });
              window.dispatchEvent(move);
              const up = new PointerEvent('pointerup', {bubbles: true, button: 0});
              window.dispatchEvent(up);
              return {beforeW, ok: true};
            }""", vid["id"])
            print("  拖拽:", res)
            time.sleep(2.5)
            # 检查 src_out 是否变大
            d2, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, vid["id"]))
            new_out = d2["data"]["video"]["src_out"]["static"]
            print("  src_out: 5.0 -> %s" % new_out)
            chk("右边缘能拉长（src_out 变大）", new_out and new_out > 5.1, new_out)

        print("=== 5. 同轨防重叠：拖动片段到另一片段位置上 ===")
        # 先放两个片段在同一条轨
        d, _ = http("GET", "/api/projects/%s" % pid)
        tracks = d["data"]["project"]["tracks"]
        vtrack = next((t for t in tracks if t["type"] == "video"), None)
        make2, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                        {"role": "video", "track_id": vtrack["id"], "start": 0.0})
        cid2 = make2["data"]["clips"][0]["id"]
        page.reload(wait_until="networkidle")
        time.sleep(2.0)

        overlap = page.evaluate("""(args) => {
          const [cidA, cidB] = args;
          const a = document.querySelector('.tl-clip[data-cid="' + cidA + '"]');
          const b = document.querySelector('.tl-clip[data-cid="' + cidB + '"]');
          if (!a || !b) return {err: 'missing', a: !!a, b: !!b};
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          // 把 b 拖到 a 中间（制造重叠）
          const down = new PointerEvent('pointerdown', {
            clientX: rb.left + rb.width/2, clientY: rb.top + rb.height/2,
            bubbles: true, button: 0 });
          b.dispatchEvent(down);
          const move = new PointerEvent('pointermove', {
            clientX: ra.left + ra.width/2, clientY: rb.top + rb.height/2,
            bubbles: true, button: 0 });
          window.dispatchEvent(move);
          const up = new PointerEvent('pointerup', {bubbles: true, button: 0});
          window.dispatchEvent(up);
          return {ok: true};
        }""", [vid["id"], cid2])
        print("  拖拽:", overlap)
        time.sleep(2.5)

        # 检查两个片段是否重叠
        d3, _ = http("GET", "/api/projects/%s/clips" % pid)
        clips = d3["data"]["clips"]
        ranges = []
        for cid, cc in clips.items():
            if cc["timeline"]["track_id"] == vtrack["id"]:
                s = cc["timeline"]["start"] or 0
                e = s + (cc["timeline"]["duration"] or 0)
                ranges.append((s, e, cid))
        ranges.sort()
        bad = False
        for i in range(len(ranges) - 1):
            if ranges[i][1] > ranges[i + 1][0] + 1e-3:
                bad = True
                print("  重叠:", ranges[i], ranges[i + 1])
        chk("同轨无重叠", not bad, ranges)

        page.screenshot(path="data/_shot_fix3.png")
        print("截图 -> data/_shot_fix3.png")
        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("FIX3 ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__