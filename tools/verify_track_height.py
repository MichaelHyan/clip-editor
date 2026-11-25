# -*- coding: utf-8 -*-
"""轨道高度可调 —— 浏览器实测。

验证项：
  1. 每个轨道头都有 .tl-head-resize 把手
  2. 向下拖把手 -> 行高变大，.tl-lane 同步变大
  3. 松手后落盘（重新读项目，height 已改）
  4. 向上拖 -> 变小
  5. 拖到极值被钳制在 28~240
  6. 双击把手 -> 恢复默认 64px
  7. 拖动把手不会误触发轨道排序
  8. 控制台无错误

安全约束：全程在临时项目里跑，结束删除，绝不碰现有项目。
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:5173/api"
BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  [OK] " if ok else "  [FAIL] ") + name + (("  -> " + str(detail)) if detail else ""))


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def new_project(name):
    return http("POST", API + "/projects", {"name": name})["data"]["project"]["id"]


def drop_project(pid):
    try:
        http("DELETE", API + "/projects/" + pid)
    except Exception:
        pass


JS_TRACKS = """
async () => {
  const m = await import('/static/js/state.js');
  return (m.state.project.tracks || [])
    .slice()
    .sort((a, b) => (a.index || 0) - (b.index || 0))
    .map(t => ({idx: t.index, type: t.type, name: t.name, id: t.id, h: t.height || 64}));
}
"""

JS_ROW_H = """
(tid) => {
  const head = document.querySelector('.tl-head[data-track="' + tid + '"]');
  const lane = document.querySelector('.tl-lane[data-track="' + tid + '"]');
  const rz = document.querySelector('.tl-head[data-track="' + tid + '"] .tl-head-resize');
  return {
    head: head ? Math.round(head.getBoundingClientRect().height) : null,
    lane: lane ? Math.round(lane.getBoundingClientRect().height) : null,
    hasHandle: !!rz,
    handleH: rz ? Math.round(rz.getBoundingClientRect().height) : 0,
  };
}
"""


def row_h(page, tid):
    return page.evaluate(JS_ROW_H, tid)


def drag(page, handle_sel, dy, steps=10):
    """按住把手垂直拖动 dy 像素。"""
    box = page.locator(handle_sel).bounding_box()
    if not box:
        return False
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x, y + dy * i / steps)
        time.sleep(0.02)
    page.mouse.up()
    return True


def main():
    pid = new_project("__track_height__")
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EDGE, headless=True)
            ctx = browser.new_context(viewport={"width": 1600, "height": 950})
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())

            page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
            time.sleep(1.5)
            page.wait_for_selector("#timeline .tl-lane", timeout=10000)

            cur = page.evaluate("async () => (await import('/static/js/state.js')).state.pid")
            rec("已切到临时项目", cur == pid, cur)
            if cur != pid:
                raise RuntimeError("项目上下文不对，中止")

            # ---------------------------------------------------------- 把手存在

            print("\n== 把手 ==")
            t0 = page.evaluate(JS_TRACKS)
            all_have = all(row_h(page, t["id"])["hasHandle"] for t in t0)
            rec("每条轨道都有高度把手", all_have, "%d 条" % len(t0))

            h0 = row_h(page, t0[0]["id"])
            rec("把手高度 5px", h0["handleH"] == 5, h0["handleH"])
            rec("初始行高 64", h0["head"] == 64, h0["head"])

            # ---------------------------------------------------------- 拖大变高

            print("\n== 向下拖动 ==")
            tid = t0[0]["id"]
            sel = '.tl-head[data-track="%s"] .tl-head-resize' % tid
            before = row_h(page, tid)
            drag(page, sel, +50)
            time.sleep(1.0)
            after = row_h(page, tid)
            rec("行高变大", after["head"] > before["head"],
                "%d -> %d" % (before["head"], after["head"]))
            rec("lane 高度同步", after["lane"] == after["head"],
                "lane=%s head=%s" % (after["lane"], after["head"]))
            rec("增量接近 50px", abs((after["head"] - before["head"]) - 50) <= 3,
                after["head"] - before["head"])

            # ---------------------------------------------------------- 落盘

            print("\n== 落盘 ==")
            time.sleep(0.6)
            pj = http("GET", API + "/projects/" + pid)["data"]
            saved = next(t for t in pj["project"]["tracks"] if t["id"] == tid)
            rec("height 已落盘", saved["height"] == after["head"],
                "disk=%s dom=%s" % (saved["height"], after["head"]))

            # 重载页面，确认持久
            page.reload(wait_until="networkidle")
            time.sleep(1.5)
            page.wait_for_selector("#timeline .tl-lane", timeout=10000)
            reloaded = row_h(page, tid)
            rec("刷新后高度保持", reloaded["head"] == after["head"],
                "reload=%s prev=%s" % (reloaded["head"], after["head"]))

            # ---------------------------------------------------------- 拖小

            print("\n== 向上拖动 ==")
            before2 = row_h(page, tid)
            drag(page, sel, -30)
            time.sleep(1.0)
            after2 = row_h(page, tid)
            rec("行高变小", after2["head"] < before2["head"],
                "%d -> %d" % (before2["head"], after2["head"]))

            # ---------------------------------------------------------- 极值钳制

            print("\n== 极值钳制 ==")
            drag(page, sel, +600, steps=14)
            time.sleep(1.0)
            big = row_h(page, tid)
            rec("上限 240", big["head"] == 240, big["head"])

            drag(page, sel, -900, steps=16)
            time.sleep(1.0)
            small = row_h(page, tid)
            rec("下限 28", small["head"] == 28, small["head"])

            # ---------------------------------------------------------- 双击恢复

            print("\n== 双击恢复 ==")
            page.dblclick(sel)
            time.sleep(1.0)
            reset = row_h(page, tid)
            rec("双击恢复 64", reset["head"] == 64, reset["head"])

            pj2 = http("GET", API + "/projects/" + pid)["data"]
            saved2 = next(t for t in pj2["project"]["tracks"] if t["id"] == tid)
            rec("恢复也落盘", saved2["height"] == 64, saved2["height"])

            # ---------------------------------------------------------- 不误触发排序

            print("\n== 不误触发排序 ==")
            order_before = [t["id"] for t in page.evaluate(JS_TRACKS)]
            drag(page, '.tl-head[data-track="%s"] .tl-head-resize' % tid, +25, steps=8)
            time.sleep(1.0)
            order_after = [t["id"] for t in page.evaluate(JS_TRACKS)]
            rec("拖动把手不改顺序", order_before == order_after,
                "%s vs %s" % (order_before[:2], order_after[:2]))

            # 恢复原状
            page.dblclick('.tl-head[data-track="%s"] .tl-head-resize' % tid)
            time.sleep(0.8)

            # ---------------------------------------------------------- 控制台

            print("\n== 控制台 ==")
            real = [e for e in errors if "favicon" not in str(e).lower()]
            if real:
                for e in real[:8]:
                    print("    ", str(e)[:200])
            rec("控制台无错误", not real, len(real))

            browser.close()
    finally:
        drop_project(pid)

    fails = [r for r in results if not r[1]]
    print("\n== 汇总：%d/%d 通过 ==" % (len(results) - len(fails), len(results)))
    if fails:
        for n, _, d in fails:
            print("  FAIL:", n, d)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

# __EOF__