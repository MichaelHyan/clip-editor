# -*- coding: utf-8 -*-
"""用系统 Edge 真实打开编辑器页面，捕获控制台错误与页面异常。"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

errors = []
console_logs = []


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()

        page.on("console", lambda m: (
            console_logs.append((m.type, m.text)),
            errors.append(("console:" + m.type, m.text)) if m.type == "error" else None,
        ))
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("requestfailed", lambda r: errors.append(
            ("requestfailed", r.url + " :: " + str(r.failure))))

        page.goto(BASE, wait_until="networkidle", timeout=30000)
        time.sleep(2.0)

        # 等待编辑器主体构建出来
        try:
            page.wait_for_selector("#timeline .tl-lane", timeout=10000)
        except Exception as e:
            errors.append(("wait_selector", "tl-lane 未出现: " + str(e)))

        info = page.evaluate("""() => {
            const q = (s) => document.querySelector(s);
            const lanes = document.querySelectorAll('.tl-lane').length;
            const heads = document.querySelectorAll('.tl-head').length;
            const clips = document.querySelectorAll('.tl-clip').length;
            const tabs = document.querySelectorAll('.insp-tab').length;
            const cv = q('#preview-canvas');
            return {
              title: document.title,
              projName: q('#proj-name') ? q('#proj-name').textContent : null,
              projMeta: q('#proj-meta') ? q('#proj-meta').textContent : null,
              lanes, heads, clips, tabs,
              canvasW: cv ? cv.width : 0,
              canvasH: cv ? cv.height : 0,
              tcCur: q('#tc-cur') ? q('#tc-cur').textContent : null,
              statusRev: q('#status-rev') ? q('#status-rev').textContent : null,
              syncDot: q('#status-sync') ? q('#status-sync').className : null,
            };
        }""")

        print("=== 页面状态 ===")
        for k, v in info.items():
            print("  %-12s %s" % (k, v))

        print("=== 控制台（除 debug 外）===")
        for t, m in console_logs[:40]:
            print("  [%s] %s" % (t, m[:200]))

        page.screenshot(path="data/_shot_editor.png", full_page=False)
        print("截图 -> data/_shot_editor.png")

        browser.close()

    print()
    if errors:
        print("发现 %d 个问题：" % len(errors))
        for k, v in errors[:30]:
            print("  [%s] %s" % (k, v[:300]))
        sys.exit(1)
    print("浏览器端无错误")


if __name__ == "__main__":
    main()


# __EOF__