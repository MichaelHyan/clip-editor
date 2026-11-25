# -*- coding: utf-8 -*-
"""轨道重命名 / 拖动排序 / 类型切换 —— 双重验证。

第一部分：API 级（快，覆盖后端校验）
第二部分：浏览器级（Playwright + 系统 Edge，覆盖 UI 真实交互）

安全约束：全程在临时项目里跑，结束删除，绝不碰现有项目。
"""
import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:5173/api"
BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SAMPLE = "D:/git/clip-editor/data/test_media/sample.mp4"

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


def http_expect_err(method, url, body=None):
    """期望返回 ok:false，返回 (status, error)。"""
    try:
        http(method, url, body)
        return None, None
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8"))
        except Exception:
            d = {}
        return e.code, d.get("error")


def new_project(name):
    return http("POST", API + "/projects", {"name": name})["data"]["project"]["id"]


def drop_project(pid):
    try:
        http("DELETE", API + "/projects/" + pid)
    except Exception:
        pass


# ================================================================ API 级

def api_checks():
    print("\n== API 级：后端校验 ==")
    pid = new_project("__track_props_api__")
    try:
        tl = http("GET", API + "/projects/" + pid)["data"]
        trs = sorted(tl["project"]["tracks"], key=lambda t: t["index"])
        vid = next(t for t in trs if t["type"] == "video")
        sub = next(t for t in trs if t["type"] == "subtitle")

        # ---- 重命名
        pj = http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
                  {"patch": {"name": "主视频轨"}})
        renamed = next(t for t in pj["data"]["tracks"] if t["id"] == vid["id"])
        rec("重命名成功", renamed["name"] == "主视频轨", renamed["name"])

        # 空名被忽略（保留原名）
        pj = http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
                  {"patch": {"name": "   "}})
        still = next(t for t in pj["data"]["tracks"] if t["id"] == vid["id"])
        rec("空名被忽略", still["name"] == "主视频轨", still["name"])

        # 超长名被截断到 40
        long_name = "x" * 80
        pj = http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
                  {"patch": {"name": long_name}})
        cut = next(t for t in pj["data"]["tracks"] if t["id"] == vid["id"])
        rec("超长名截断到 40", len(cut["name"]) == 40, len(cut["name"]))

        # ---- 类型切换：空轨可改
        pj = http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
                  {"patch": {"type": "audio", "name": "改后音频"}})
        changed = next(t for t in pj["data"]["tracks"] if t["id"] == vid["id"])
        rec("空轨可切换类型", changed["type"] == "audio", changed["type"])

        # 改回来
        http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
             {"patch": {"type": "video", "name": "V-test"}})

        # ---- 非法类型被拒
        code, err = http_expect_err(
            "PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
            {"patch": {"type": "garbage"}})
        rec("非法类型被拒 400", code == 400, "%s %s" % (code, err))

        # ---- 非空轨不能改类型
        http("POST", API + "/projects/" + pid + "/clips",
             {"path": SAMPLE, "track_id": vid["id"]})
        code, err = http_expect_err(
            "PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
            {"patch": {"type": "audio"}})
        rec("非空轨改类型被拒 400", code == 400, "%s %s" % (code, (err or "")[:60]))

        # 改回原类型（同类型不算改，应通过）
        pj = http("PATCH", API + "/projects/" + pid + "/tracks/" + vid["id"],
                  {"patch": {"type": "video"}})
        rec("同类型 patch 不被拒", pj["ok"])

        # ---- 排序
        order = [sub["id"], vid["id"]] + [t["id"] for t in trs
                                          if t["id"] not in (sub["id"], vid["id"])]
        pj = http("POST", API + "/projects/" + pid + "/tracks/reorder",
                  {"order": order})
        after = sorted(pj["data"]["tracks"], key=lambda t: t["index"])
        rec("排序生效（subtitle 排第一）", after[0]["id"] == sub["id"],
            [t["type"] for t in after])
        rec("index 连续", [t["index"] for t in after] == list(range(len(after))),
            [t["index"] for t in after])

        # 排序传不完整 order：剩余轨应追加在后面而不是丢失
        pj = http("POST", API + "/projects/" + pid + "/tracks/reorder",
                  {"order": [vid["id"]]})
        after2 = pj["data"]["tracks"]
        rec("不完整 order 不丢轨道", len(after2) == len(after),
            "%d vs %d" % (len(after2), len(after)))
        rec("指定轨排第一", after2[0]["id"] == vid["id"], after2[0]["name"])

    finally:
        drop_project(pid)


# ================================================================ 浏览器级

JS_TRACKS = """
async () => {
  const m = await import('/static/js/state.js');
  return (m.state.project.tracks || [])
    .slice()
    .sort((a, b) => (a.index || 0) - (b.index || 0))
    .map(t => ({idx: t.index, type: t.type, name: t.name, id: t.id, clips: (t.clips||[]).length}));
}
"""


def browser_checks():
    from playwright.sync_api import sync_playwright

    print("\n== 浏览器级：真实交互 ==")
    pid = new_project("__track_props_ui__")
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

            # ---- 双击重命名
            print("  -- 双击重命名 --")
            t0 = page.evaluate(JS_TRACKS)
            vid = next(t for t in t0 if t["type"] == "video")
            page.dblclick('.tl-head[data-track="%s"] .tl-head-name' % vid["id"])
            time.sleep(0.4)
            input_sel = '.tl-head[data-track="%s"] .tl-head-name-input' % vid["id"]
            rec("出现重命名输入框", page.locator(input_sel).count() == 1)

            page.fill(input_sel, "主画面")
            page.keyboard.press("Enter")
            time.sleep(1.0)
            t1 = page.evaluate(JS_TRACKS)
            renamed = next(t for t in t1 if t["id"] == vid["id"])
            rec("回车后重命名生效", renamed["name"] == "主画面", renamed["name"])

            # Esc 取消
            page.dblclick('.tl-head[data-track="%s"] .tl-head-name' % vid["id"])
            time.sleep(0.4)
            page.fill(input_sel, "不要保存")
            page.keyboard.press("Escape")
            time.sleep(0.8)
            t2 = page.evaluate(JS_TRACKS)
            rec("Esc 取消不保存",
                next(t for t in t2 if t["id"] == vid["id"])["name"] == "主画面")

            # ---- 类型切换（空轨）
            print("  -- 类型切换 --")
            t3 = page.evaluate(JS_TRACKS)
            empty_vid = next((t for t in t3 if t["type"] == "video" and t["clips"] == 0), None)
            if empty_vid:
                btn = page.locator('.tl-head[data-track="%s"] button[title*="切换类型"]'
                                   % empty_vid["id"])
                rec("空视频轨有切换按钮", btn.count() == 1, btn.count())
                btn.first.click()
                time.sleep(1.2)
                t4 = page.evaluate(JS_TRACKS)
                after = next((t for t in t4 if t["id"] == empty_vid["id"]), None)
                rec("空轨切换成功", bool(after) and after["type"] == "audio",
                    after["type"] if after else None)
                rec("切换后重命名", bool(after) and after["name"].startswith("A"),
                    after["name"] if after else None)

            # ---- 非空轨切换被拦
            print("  -- 非空轨切换被拦 --")
            t5 = page.evaluate(JS_TRACKS)
            target_v = next((t for t in t5 if t["type"] == "video"), None)
            if target_v:
                http("POST", API + "/projects/" + pid + "/clips",
                     {"path": SAMPLE, "track_id": target_v["id"]})
                page.evaluate("async () => { const m = await import('/static/js/state.js'); await m.refresh(); }")
                time.sleep(1.0)
                before_type = page.evaluate(JS_TRACKS)
                before_v = next((t for t in before_type if t["id"] == target_v["id"]), None)
                page.locator('.tl-head[data-track="%s"] button[title*="切换类型"]'
                             % target_v["id"]).first.click()
                time.sleep(1.2)
                after_type = page.evaluate(JS_TRACKS)
                after_v = next((t for t in after_type if t["id"] == target_v["id"]), None)
                rec("非空轨类型不变",
                    bool(before_v) and bool(after_v) and before_v["type"] == after_v["type"],
                    "%s -> %s" % (before_v["type"] if before_v else "?", after_v["type"] if after_v else "?"))

            # ---- 拖动排序
            print("  -- 拖动排序 --")
            t6 = page.evaluate(JS_TRACKS)
            if len(t6) >= 3:
                first_id = t6[0]["id"]
                third_id = t6[2]["id"]
                src = page.locator('.tl-head[data-track="%s"] .tl-head-name' % first_id)
                dst = page.locator('.tl-head[data-track="%s"] .tl-head-name' % third_id)
                sb = src.bounding_box()
                db = dst.bounding_box()
                if sb and db:
                    page.mouse.move(sb["x"] + sb["width"] / 2, sb["y"] + sb["height"] / 2)
                    page.mouse.down()
                    # 分多步移动，确保超过 4px 阈值并触发插入线
                    steps = 8
                    for i in range(1, steps + 1):
                        y = sb["y"] + (db["y"] + db["height"] * 0.8 - sb["y"]) * i / steps
                        page.mouse.move(sb["x"] + sb["width"] / 2, y)
                        time.sleep(0.03)
                    page.mouse.up()
                    time.sleep(1.2)
                    t7 = page.evaluate(JS_TRACKS)
                    moved = t7[0]["id"] != first_id
                    rec("拖动后顺序改变", moved,
                        "拖动前首轨 %s，拖动后首轨 %s" % (first_id, t7[0]["id"]))
                    rec("轨道总数不变", len(t7) == len(t6), "%d vs %d" % (len(t7), len(t6)))
                    rec("index 仍连续",
                        [t["idx"] for t in t7] == list(range(len(t7))),
                        [t["idx"] for t in t7])

            # ---- 汇总控制台错误
            print("  -- 控制台 --")
            real = [e for e in errors if "favicon" not in str(e).lower()]
            if real:
                for e in real[:8]:
                    print("    ", str(e)[:200])
            rec("控制台无错误", not real, len(real))

            browser.close()
    finally:
        drop_project(pid)


def main():
    api_checks()
    browser_checks()

    fails = [r for r in results if not r[1]]
    print("\n== 汇总：%d/%d 通过 ==" % (len(results) - len(fails), len(results)))
    if fails:
        print("失败项：")
        for n, _, d in fails:
            print("  -", n, d)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

# __EOF__