# -*- coding: utf-8 -*-
"""读取 preview.js 内部调试状态，定位渲染 bail 原因。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PROJECT = "proj_00c13582b005"


def main():
    logs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        page.on("pageerror", lambda e: logs.append("[PAGEERROR] " + str(e)[:300]))
        page.on("console", lambda m: logs.append("[%s] %s" % (m.type, m.text[:250])))

        page.goto(BASE + "/?p=" + PROJECT, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)

        # 把播放头移到 3s（片段内部）
        page.evaluate("""() => {
          const r = document.querySelector('.tl-ruler').getBoundingClientRect();
          const ev = new PointerEvent('pointerdown', {clientX: r.left + 120, clientY: r.top + 8, bubbles: true, button: 0});
          document.querySelector('.tl-ruler').dispatchEvent(ev);
        }""")
        time.sleep(2.0)

        print("=== A. window.__previewDebug 是否挂载 ===")
        has = page.evaluate("() => !!window.__previewDebug")
        print("   mounted:", has)
        if not has:
            print("   日志:", logs[-15:])
            browser.close()
            return

        print("=== B. 状态 ===")
        print(json.dumps(page.evaluate("() => window.__previewDebug.state()"), ensure_ascii=False))

        print("=== C. 媒体缓存 ===")
        print(json.dumps(page.evaluate("() => window.__previewDebug.cache()"),
                         ensure_ascii=False, indent=2))

        print("=== D. bail 原因 ===")
        print(json.dumps(page.evaluate("() => window.__previewDebug.dbg.bail"),
                         ensure_ascii=False, indent=2))

        print("=== E. lastDrawn ===")
        print(json.dumps(page.evaluate("() => window.__previewDebug.dbg.lastDrawn"),
                         ensure_ascii=False, indent=2))

        print("=== F. 强制渲染一次后再看 ===")
        page.evaluate("() => window.__previewDebug.render()")
        time.sleep(0.5)
        print("   bail:", json.dumps(page.evaluate("() => window.__previewDebug.dbg.bail"),
                                     ensure_ascii=False))
        px = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          const g = cv.getContext('2d');
          const d = g.getImageData(500, 400, 60, 60).data;
          let nz = 0;
          for (let i = 0; i < d.length; i += 4) if (d[i]>8||d[i+1]>8||d[i+2]>8) nz++;
          return nz;
        }""")
        print("   非黑像素:", px, "/ 3600")

        print("=== G. 控制台 ===")
        for line in logs[-20:]:
            print("  ", line)

        browser.close()


if __name__ == "__main__":
    main()

# __EOF__