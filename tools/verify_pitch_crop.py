# -*- coding: utf-8 -*-
"""补验证：音高不改变速度 + 裁剪保持比例（视觉）。"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SRC = "D:/git/clip-editor/data/test_media/sample.mp4"   # 1280x720，色彩条

FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def sample_canvas(page):
    """返回画面内容的包围盒（非黑区域）与整体尺寸，用来判断是否保持比例。"""
    return page.evaluate("""() => {
      const cv = document.getElementById('preview-canvas');
      const g = cv.getContext('2d');
      const W = cv.width, H = cv.height;
      const d = g.getImageData(0, 0, W, H).data;
      let minX = W, maxX = -1, minY = H, maxY = -1, nz = 0;
      for (let y = 0; y < H; y += 3) {
        for (let x = 0; x < W; x += 3) {
          const i = (y * W + x) * 4;
          if (d[i] > 8 || d[i+1] > 8 || d[i+2] > 8) {
            nz++;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
          }
        }
      }
      if (maxX < 0) return {empty: true, nz: 0};
      return {
        empty: false, nz,
        box: {x: minX, y: minY, w: maxX - minX, h: maxY - minY},
        ratio: (maxX - minX) / Math.max(1, maxY - minY),
        canvas: {w: W, h: H},
      };
    }""")


def goto_t(page, t):
    page.evaluate("""(t) => {
      const r = document.querySelector('.tl-ruler').getBoundingClientRect();
      document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent('pointerdown', {
        clientX: r.left + t * 80, clientY: r.top + 8, bubbles: true, button: 0}));
      window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
    }""", t)
    time.sleep(2.0)


def main():
    print("=== 准备 ===")
    pj = http("POST", "/api/projects", {"name": "音高裁剪验证", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    made = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 0.0, "split_av": True})["data"]["clips"]
    vclip = next(c for c in made if c["media"]["kind"] == "video")
    aclip = next(c for c in made if c["media"]["kind"] == "audio")
    print("  pid=%s" % pid)

    print("=== 问题3：音高不改变音频速度 ===")
    # 音源 10s，speed=1，音高 +12 半音。若音高改速度，实际播放时长会变 5s。
    # 通过 audio.js 的 activeSources 检查 playbackRate 与 detune 的净速率。
    http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
         {"patch": {"audio.pitch": 12}})

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        page.click("#preview-canvas")
        time.sleep(0.3)
        page.keyboard.press("Space")
        time.sleep(2.0)

        st = page.evaluate("() => window.__audioDebug ? window.__audioDebug.state() : null")
        chk("音频调试钩子可用", st is not None)
        if st and st.get("sources"):
            s0 = st["sources"][0]
            rate = s0["rate"]
            detune = s0["detune"]
            net = rate * (2 ** (detune / 1200))
            print("  playbackRate=%.4f detune=%.1f 净速率=%.4f" % (rate, detune, net))
            chk("音高 +12 半音：detune=1200", abs(detune - 1200) < 50, detune)
            chk("playbackRate 反向补偿到 0.5", abs(rate - 0.5) < 0.05, rate)
            chk("净速率仍为 1.0（速度不变）", abs(net - 1.0) < 0.06, net)

        page.keyboard.press("Space")
        time.sleep(0.5)

        print("=== 问题4：裁剪保持比例 ===")
        # 先把音高还原，避免干扰
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
             {"patch": {"audio.pitch": 0}})

        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        goto_t(page, 3.0)

        base = sample_canvas(page)
        print("  未裁剪: box=%s ratio=%.3f" % (base.get("box"), base.get("ratio", 0)))
        chk("基准画面有内容", not base.get("empty"), base)
        chk("基准比例接近 16:9", abs(base["ratio"] - 16/9) < 0.2, base["ratio"])

        # 裁掉左右各 25% -> 剩下中间 50% 宽度，比例应变成约 8:9
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
             {"patch": {"video.crop.left": 0.25, "video.crop.right": 0.25}})
        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        goto_t(page, 3.0)

        cropped = sample_canvas(page)
        print("  裁剪后: box=%s ratio=%.3f" % (cropped.get("box"), cropped.get("ratio", 0)))
        chk("裁剪后仍有画面", not cropped.get("empty"), cropped)

        # 关键判据：宽高比变了 = 保持了裁剪形状；没变 = 被拉伸回原尺寸
        chk("裁剪后宽高比变小（未拉伸）",
            cropped.get("ratio", 1) < base["ratio"] * 0.75,
            {"base": base["ratio"], "cropped": cropped.get("ratio")})
        # 理想值：16:9 * 0.5 = 8:9 ≈ 0.889
        print("  期望比例 ≈ %.3f" % (16/9 * 0.5))
        chk("比例接近 8:9", abs(cropped.get("ratio", 0) - 16/9*0.5) < 0.12,
            cropped.get("ratio"))

        page.screenshot(path="data/_shot_crop.png")
        print("截图 -> data/_shot_crop.png")

        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("PITCH & CROP ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__