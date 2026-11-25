# -*- coding: utf-8 -*-
"""最终验证：用彩色素材证明渲染链路正常，用黑底字素材证明是内容特性。

判据修正：
  - 黑底白字内容 ratio 天然低（白字只占 7%），不能要求 ratio > 0.15
  - 真正的判据是 colors（色彩层次）和 videoPixels（元素本身有无帧）
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
COLOR_SRC = "D:/git/clip-editor/data/test_media/sample.mp4"   # 彩色条测试图
DARK_SRC = "D:/a/standard/zzst.mp4"                            # 黑底白字片头

FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def canvas(page):
    return page.evaluate("""() => {
      const cv = document.getElementById('preview-canvas');
      const g = cv.getContext('2d');
      const d = g.getImageData(0, 0, cv.width, cv.height).data;
      let nz = 0, total = 0;
      const colors = new Set();
      for (let i = 0; i < d.length; i += 4 * 29) {
        const r=d[i], gg=d[i+1], b=d[i+2];
        total++;
        if (r>8||gg>8||b>8) nz++;
        colors.add(r+','+gg+','+b);
      }
      return {ratio: +(nz/total).toFixed(3), colors: colors.size};
    }""")


def video_pixels(page):
    out = page.evaluate("() => window.__previewDebug ? window.__previewDebug.probeFrame() : []")
    return out[0] if out else {}


def goto_t(page, t, pps=80):
    page.evaluate("""(arg) => {
      const el = document.querySelector('.tl-ruler');
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent('pointerdown', {
        clientX: r.left + arg.t * arg.pps, clientY: r.top + 8, bubbles: true, button: 0 }));
      window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
    }""", {"t": t, "pps": pps})
    time.sleep(2.0)


def main():
    print("=== 建对照项目：彩色 + 黑底字 ===")
    pj = http("POST", "/api/projects", {"name": "渲染验证", "settings": {"fps": 30}})["data"]
    pid = pj["project"]["id"]

    c_color = http("POST", "/api/projects/%s/clips" % pid, {"path": COLOR_SRC})["data"]
    c_dark = http("POST", "/api/projects/%s/clips" % pid, {"path": DARK_SRC, "start": 15.0})["data"]
    print("  彩色片段:", c_color["id"], "时长", round(c_color["timeline"]["duration"], 1))
    print("  黑底片段:", c_dark["id"], "时长", round(c_dark["timeline"]["duration"], 1))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(3.0)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        print("=== 1. 彩色素材（色彩条）2s 处 ===")
        goto_t(page, 2.0)
        s = canvas(page)
        v = video_pixels(page)
        print("   canvas:", s, "| videoPixels:", v.get("videoPixels"))
        chk("彩色画面出图", s["ratio"] > 0.5, s["ratio"])
        chk("色彩丰富", s["colors"] > 500, s["colors"])
        chk("video 元素有帧", v.get("videoPixels", 0) > 1000, v.get("videoPixels"))

        print("=== 2. 彩色素材连续 seek 都有画面 ===")
        ok = 0
        for tt in [1.0, 3.0, 5.0, 7.0]:
            goto_t(page, tt)
            s2 = canvas(page)
            print("   t=%d: ratio=%.3f colors=%d" % (tt, s2["ratio"], s2["colors"]))
            if s2["ratio"] > 0.5:
                ok += 1
        chk("连续 seek 全部有画面", ok == 4, ok)

        print("=== 3. 黑底白字素材（片头标题 18.5s）===")
        goto_t(page, 18.5)
        s3 = canvas(page)
        v3 = video_pixels(page)
        print("   canvas:", s3, "| videoPixels:", v3.get("videoPixels"))
        chk("video 元素有帧（不是解码失败）", v3.get("videoPixels", 0) > 50, v3.get("videoPixels"))
        chk("有灰度层次（白字抗锯齿）", s3["colors"] > 30, s3["colors"])
        chk("黑底内容 ratio 天然低", s3["ratio"] < 0.3, s3["ratio"])

        print("=== 4. 播放彩色素材 ===")
        goto_t(page, 1.0)
        page.keyboard.press("Space")
        time.sleep(0.8)
        sa = canvas(page)
        time.sleep(1.0)
        sb = canvas(page)
        page.keyboard.press("Space")
        chk("播放中有画面", sa["ratio"] > 0.3 and sb["ratio"] > 0.3, (sa["ratio"], sb["ratio"]))
        chk("画面在动", sa["colors"] != sb["colors"] or sa["ratio"] != sb["ratio"], (sa, sb))

        page.screenshot(path="data/_shot_color.png")
        print("截图 -> data/_shot_color.png")

        # 切到黑底片段截图
        goto_t(page, 18.5)
        page.screenshot(path="data/_shot_dark.png")
        print("截图 -> data/_shot_dark.png")

        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("RENDER VERIFY ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__