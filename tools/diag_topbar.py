# -*- coding: utf-8 -*-
"""诊断顶栏元素是否在 DOM 中、是否有 JS 错误。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"


def main():
    import urllib.request
    with urllib.request.urlopen(BASE + "/api/projects", timeout=20) as r:
        projects = json.loads(r.read().decode("utf-8"))["data"]
    pid = None
    for p in projects:
        if p.get("clip_count", 0) > 0:
            pid = p["id"]
            break
    print("项目:", pid)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:400]))
        page.on("console", lambda m: errs.append("[console:" + m.type + "] " + m.text[:200])
                if m.type == "error" else None)

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(3.0)

        print("=== DOM 检查 ===")
        st = page.evaluate("""() => {
          const q = (s) => document.querySelector(s);
          const top = q('#topbar');
          return {
            hasTopbar: !!top,
            hasSwitch: !!q('#proj-switch'),
            hasSwitchBtn: !!q('#proj-switch-btn'),
            hasMenu: !!q('#proj-menu'),
            hasName: !!q('#proj-name'),
            topHTML: top ? top.innerHTML.slice(0, 400) : null,
          };
        }""")
        print(json.dumps(st, ensure_ascii=False, indent=2))

        print("=== JS 错误 ===")
        if errs:
            for e in errs[:10]:
                print("  ", e)
        else:
            print("  无")

        browser.close()


if __name__ == "__main__":
    main()

# __EOF__