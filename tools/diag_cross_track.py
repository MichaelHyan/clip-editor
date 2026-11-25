# -*- coding: utf-8 -*-
"""探测跨轨拖拽为何不生效：elementFromPoint 返回什么、onDragMove 是否被调用。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

PID = None


def main():
    import urllib.request
    # 找一个含片段的项目
    with urllib.request.urlopen(BASE + "/api/projects", timeout=20) as r:
        projects = json.loads(r.read().decode("utf-8"))["data"]
    pid = None
    for p in projects:
        if p.get("clip_count", 0) > 0:
            pid = p["id"]
            break
    if not pid:
        print("没有含片段的项目")
        return
    print("项目:", pid)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        info = page.evaluate("""() => {
          const clips = [...document.querySelectorAll('.tl-clip')];
          const lanes = [...document.querySelectorAll('.tl-lane')].map(l => {
            const b = l.getBoundingClientRect();
            return {type: l.dataset.type, track: l.dataset.track,
                    x: b.x, y: b.y, w: b.width, h: b.height};
          });
          const c0 = clips[0];
          const b = c0.getBoundingClientRect();
          return {
            clip: {cid: c0.dataset.cid, x: b.x, y: b.y, w: b.width, h: b.height,
                   track: c0.closest('.tl-lane').dataset.track},
            lanes,
          };
        }""")
        print("片段:", {k: (round(v, 1) if isinstance(v, float) else v)
                       for k, v in info["clip"].items()})
        print("轨道:")
        for l in info["lanes"]:
            print("  %-6s %s  y=%.0f h=%.0f" % (l["type"], l["track"], l["y"], l["h"]))

        vlanes = [l for l in info["lanes"] if l["type"] == "video"]
        if len(vlanes) < 2:
            print("只有 %d 条视频轨，无法测跨轨" % len(vlanes))
            browser.close()
            return

        cur = next((l for l in vlanes if l["track"] == info["clip"]["track"]), vlanes[0])
        dst = next((l for l in vlanes if l["track"] != info["clip"]["track"]), None)
        if not dst:
            print("没有其他视频轨")
            browser.close()
            return

        sx = info["clip"]["x"] + info["clip"]["w"] * 0.5
        sy = info["clip"]["y"] + info["clip"]["h"] * 0.5
        ty = dst["y"] + dst["h"] * 0.5

        print("\n从 (%.0f,%.0f) 拖到 (%.0f,%.0f)" % (sx, sy, sx, ty))

        # 埋点：拦截 elementFromPoint
        page.evaluate("""() => {
          window.__probe = {calls: [], dragClass: [], renders: 0};
          const orig = document.elementFromPoint.bind(document);
          document.elementFromPoint = (x, y) => {
            const el = orig(x, y);
            window.__probe.calls.push({
              x: Math.round(x), y: Math.round(y),
              tag: el ? el.tagName : null,
              cls: el ? String(el.className).slice(0, 40) : null,
              lane: el && el.closest ? (el.closest('.tl-lane') ?
                el.closest('.tl-lane').dataset.track : null) : null,
            });
            return el;
          };
        }""")

        page.mouse.move(sx, sy)
        page.mouse.down()
        time.sleep(0.3)

        # 检查拖拽类是否真的加到了 DOM 节点上
        cls = page.evaluate("""() => {
          const withCls = [...document.querySelectorAll('.dragging-clip')];
          const all = [...document.querySelectorAll('.tl-clip')];
          return {
            draggingCount: withCls.length,
            clipCount: all.length,
            clipClasses: all.slice(0, 3).map(n => String(n.className)),
          };
        }""")
        print("拖拽类状态:", cls)

        for i in range(1, 9):
            page.mouse.move(sx + 10 * i / 8, sy + (ty - sy) * i / 8)
            time.sleep(0.05)
        time.sleep(0.3)

        probe = page.evaluate("() => window.__probe")
        print("\nelementFromPoint 调用 %d 次，前 6 次:" % len(probe["calls"]))
        for c in probe["calls"][:6]:
            print("  ", c)

        hl = page.evaluate("() => document.querySelector('.tl-lane.drop-target') ? "
                          "document.querySelector('.tl-lane.drop-target').dataset.track : null")
        print("高亮轨道:", hl)

        page.mouse.up()
        time.sleep(1.0)

        browser.close()


if __name__ == "__main__":
    main()

# __EOF__