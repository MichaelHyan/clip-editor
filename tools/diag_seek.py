# -*- coding: utf-8 -*-
"""验证 seek 修复：看 video 元素本身有无帧，以及最终 canvas 是否出画。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PROJECT = "proj_00c13582b005"


def sample_canvas(page, label):
    px = page.evaluate("""() => {
      const cv = document.getElementById('preview-canvas');
      const g = cv.getContext('2d');
      const d = g.getImageData(400, 300, 200, 200).data;
      let nz = 0, total = 0;
      const colors = new Set();
      for (let i = 0; i < d.length; i += 4) {
        const r=d[i], gg=d[i+1], b=d[i+2];
        total++;
        if (r>8||gg>8||b>8) nz++;
        colors.add(r+','+gg+','+b);
      }
      return {nz, total, ratio: +(nz/total).toFixed(3), colors: colors.size};
    }""")
    print("   %s canvas: %s" % (label, px))
    return px


def main():
    logs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        page.on("pageerror", lambda e: logs.append("[PAGEERROR] " + str(e)[:300]))

        page.goto(BASE + "/?p=" + PROJECT, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)

        print("=== 1. 初始状态（播放头 0）===")
        print("   probe:", json.dumps(page.evaluate("() => window.__previewDebug.probeFrame()"),
                                    ensure_ascii=False))
        sample_canvas(page, "t=0  ")

        print("=== 2. 移到 3s 并等待 seek 完成 ===")
        page.evaluate("""() => {
          const r = document.querySelector('.tl-ruler').getBoundingClientRect();
          document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent('pointerdown', {
            clientX: r.left + 300, clientY: r.top + 8, bubbles: true, button: 0 }));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""")
        time.sleep(2.5)
        st = page.evaluate("() => window.__previewDebug.state()")
        print("   playhead:", st["playhead"])
        print("   probe:", json.dumps(page.evaluate("() => window.__previewDebug.probeFrame()"),
                                    ensure_ascii=False))
        s1 = sample_canvas(page, "t=3  ")

        print("=== 3. 再移到 10s ===")
        page.evaluate("""() => {
          const r = document.querySelector('.tl-ruler').getBoundingClientRect();
          document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent('pointerdown', {
            clientX: r.left + 900, clientY: r.top + 8, bubbles: true, button: 0 }));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""")
        time.sleep(2.5)
        st = page.evaluate("() => window.__previewDebug.state()")
        print("   playhead:", st["playhead"])
        print("   probe:", json.dumps(page.evaluate("() => window.__previewDebug.probeFrame()"),
                                    ensure_ascii=False))
        s2 = sample_canvas(page, "t=10 ")

        print("=== 4. 播放 1.5s ===")
        page.keyboard.press("Space")
        time.sleep(1.5)
        page.keyboard.press("Space")
        time.sleep(0.5)
        s3 = sample_canvas(page, "播放中")
        print("   probe:", json.dumps(page.evaluate("() => window.__previewDebug.probeFrame()"),
                                    ensure_ascii=False))

        page.screenshot(path="data/_shot_seek_fix.png")

        print()
        ok = s1["ratio"] > 0.2 and s2["ratio"] > 0.2 and s1["colors"] > 20
        print("结果:", "PASS 画面正常" if ok else "FAIL 仍有问题")
        if logs:
            print("页面错误:", logs[:5])
        browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__