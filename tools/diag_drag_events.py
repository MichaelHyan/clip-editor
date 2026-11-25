# -*- coding: utf-8 -*-
"""定位 onClipDown 是否抛错、onDragMove 是否被调用。"""
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
    if not pid:
        print("无含片段项目")
        return
    print("项目:", pid)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()

        errors = []
        logs = []
        page.on("pageerror", lambda e: errors.append(str(e)[:400]))
        page.on("console", lambda m: logs.append("[%s] %s" % (m.type, m.text[:200])))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        # 埋点：包一层 window 事件计数
        page.evaluate("""() => {
          window.__diag = {down: 0, move: 0, up: 0, addCls: 0};
          window.addEventListener('pointerdown', () => window.__diag.down++, true);
          window.addEventListener('pointermove', () => window.__diag.move++, true);
          window.addEventListener('pointerup', () => window.__diag.up++, true);

          // 监视 classList.add 调用
          const origAdd = DOMTokenList.prototype.add;
          DOMTokenList.prototype.add = function(...args) {
            if (args.includes('dragging-clip')) window.__diag.addCls++;
            return origAdd.apply(this, args);
          };
        }""")

        info = page.evaluate("""() => {
          const c = document.querySelector('.tl-clip');
          const b = c.getBoundingClientRect();
          return {cid: c.dataset.cid, x: b.x + b.width/2, y: b.y + b.height/2,
                  w: b.width, h: b.height,
                  laneY: (l => l.y + l.height/2)(c.closest('.tl-lane').getBoundingClientRect())};
        }""")
        print("片段中心:", round(info["x"]), round(info["y"]), "自身轨道 y:", round(info["laneY"]))

        page.mouse.move(info["x"], info["y"])
        time.sleep(0.1)
        page.mouse.down()
        time.sleep(0.3)

        d = page.evaluate("() => window.__diag")
        print("pointerdown 后:", d)
        n_cls = page.evaluate("() => document.querySelectorAll('.dragging-clip').length")
        print("dragging-clip 节点数:", n_cls)

        # 向下移动 80px
        for i in range(1, 11):
            page.mouse.move(info["x"], info["y"] + 8 * i)
            time.sleep(0.05)
        time.sleep(0.3)

        d2 = page.evaluate("() => window.__diag")
        print("移动后:", d2)
        hl = page.evaluate("() => { const e = document.querySelector('.tl-lane.drop-target'); "
                          "return e ? e.dataset.track : null; }")
        print("高亮:", hl)

        page.mouse.up()
        time.sleep(0.5)

        print()
        if errors:
            print("页面错误 %d 条:" % len(errors))
            for e in errors[:6]:
                print("  ", e)
        else:
            print("无页面错误")

        print()
        print("控制台尾 10 条:")
        for l in logs[-10:]:
            print("  ", l)

        browser.close()


if __name__ == "__main__":
    main()

# __EOF__