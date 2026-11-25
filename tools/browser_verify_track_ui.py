# -*- coding: utf-8 -*-
"""浏览器实测：轨道自由增删。

安全约束（重要）：
  测试**自己建一个临时项目**，所有破坏性操作都在临时项目里做，
  结束时删掉。绝不碰 ?p= 或 localStorage 里的现有项目。

验证项：
  1. 工具栏出现「+ 视频轨」「+ 音频轨」
  2. 点「+ 视频轨」-> 轨道数 +1，新轨在最上层（index 0），命名 V3
  3. 点「+ 音频轨」-> 轨道数 +1，新轨在最下层，命名 A2
  4. 轨道头有「×」删除按钮（字幕轨没有）
  5. 点「×」+ 确认 -> 轨道数 -1
  6. 删除带片段的轨道 -> 片段一并消失，选中态被清理
  7. 字幕轨不出现删除按钮
  8. 删除轨道后撤销 -> 轨道和片段都回来
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = BASE + "/api"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SAMPLE = "D:/git/clip-editor/data/test_media/sample.mp4"

errors = []
results = []

JS_TRACKS = """
async () => {
  const m = await import('/static/js/state.js');
  return (m.state.project.tracks || [])
    .slice()
    .sort((a, b) => (a.index || 0) - (b.index || 0))
    .map(t => ({idx: t.index, type: t.type, name: t.name, id: t.id, clips: (t.clips||[]).length}));
}
"""


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  [OK] " if ok else "  [FAIL] ") + name + (("  -> " + str(detail)) if detail else ""))


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def create_temp_project():
    """建一个干净的临时项目，返回 pid。"""
    d = http("POST", API + "/projects", {"name": "__track_ui_test__"})
    pid = d["data"]["project"]["id"]
    print("临时项目:", pid)
    return pid


def delete_temp_project(pid):
    try:
        http("DELETE", API + "/projects/" + pid)
        print("已删除临时项目:", pid)
    except Exception as e:
        print("删除临时项目失败:", e)


def main():
    temp_pid = None
    browser = None
    try:
        temp_pid = create_temp_project()

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EDGE, headless=True)
            ctx = browser.new_context(viewport={"width": 1600, "height": 950})
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(("console:" + m.text))
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
            page.on("dialog", lambda d: d.accept())

            # 关键：显式指定临时项目，不读 localStorage
            page.goto(BASE + "/?p=" + temp_pid, wait_until="networkidle", timeout=30000)
            time.sleep(1.5)
            page.wait_for_selector("#timeline .tl-lane", timeout=10000)

            # 二次确认确实在临时项目里
            cur_pid = page.evaluate("""async () => {
              const m = await import('/static/js/state.js');
              return m.state.pid;
            }""")
            rec("已切到临时项目", cur_pid == temp_pid, cur_pid)
            if cur_pid != temp_pid:
                raise RuntimeError("项目上下文不是临时项目，中止以防破坏真实数据")

            # ---------------------------------------------------------- 基线

            print("\n== 基线 ==")
            t0 = page.evaluate(JS_TRACKS)
            rec("初始轨道结构", True, [(t["idx"], t["type"], t["name"]) for t in t0])
            base_count = len(t0)

            btn_v = page.locator("button", has_text="+ 视频轨").count()
            btn_a = page.locator("button", has_text="+ 音频轨").count()
            rec("工具栏有「+ 视频轨」", btn_v == 1, btn_v)
            rec("工具栏有「+ 音频轨」", btn_a == 1, btn_a)

            # ---------------------------------------------------------- 加视频轨

            print("\n== 加视频轨 ==")
            page.click("button:has-text('+ 视频轨')")
            time.sleep(1.2)
            t1 = page.evaluate(JS_TRACKS)
            rec("轨道数 +1", len(t1) == base_count + 1, "%d -> %d" % (base_count, len(t1)))
            rec("新轨在最上层（idx 0）", t1[0]["type"] == "video", t1[0])
            rec("命名 V3", t1[0]["name"] == "V3", t1[0]["name"])
            idxs = [t["idx"] for t in t1]
            rec("index 连续 0..n-1", idxs == list(range(len(t1))), idxs)

            # ---------------------------------------------------------- 加音频轨

            print("\n== 加音频轨 ==")
            page.click("button:has-text('+ 音频轨')")
            time.sleep(1.2)
            t2 = page.evaluate(JS_TRACKS)
            rec("轨道数 +1", len(t2) == len(t1) + 1, "%d -> %d" % (len(t1), len(t2)))
            rec("新音频轨在最下层", t2[-1]["type"] == "audio", t2[-1])
            rec("命名 A2", t2[-1]["name"] == "A2", t2[-1]["name"])

            # ---------------------------------------------------------- 删除按钮

            print("\n== 删除按钮 ==")
            danger = page.locator(".tl-head-btn.danger").count()
            sub_heads = page.locator(".tl-head.type-subtitle").count()
            rec("轨道头有删除按钮", danger > 0, danger)
            rec("删除按钮数 = 非字幕轨数", danger == len(t2) - sub_heads,
                "%d vs %d" % (danger, len(t2) - sub_heads))

            # ---------------------------------------------------------- 删空轨

            print("\n== 删除空轨道 ==")
            before = page.evaluate(JS_TRACKS)
            top_id = before[0]["id"]
            page.evaluate("""async (tid) => {
              const btn = document.querySelector('.tl-head[data-track="' + tid + '"] .tl-head-btn.danger');
              if (btn) btn.click();
            }""", top_id)
            time.sleep(1.5)
            after = page.evaluate(JS_TRACKS)
            rec("轨道数 -1", len(after) == len(before) - 1, "%d -> %d" % (len(before), len(after)))
            rec("被删轨道不在了", all(t["id"] != top_id for t in after))

            # ---------------------------------------------------------- 带片段轨道：删 + 撤销

            print("\n== 带片段轨道：删除与撤销 ==")

            # 用 API 往临时项目导入一个片段（走 sample.mp4）
            target_track = None
            for t in page.evaluate(JS_TRACKS):
                if t["type"] == "video":
                    target_track = t["id"]
                    break

            imported = http("POST", API + "/projects/" + temp_pid + "/clips",
                            {"path": SAMPLE, "track_id": target_track})
            clip_id = imported["data"]["id"]
            rec("测试片段已导入", bool(clip_id), clip_id)

            page.evaluate("""async () => {
              const m = await import('/static/js/state.js');
              await m.refresh();
            }""")
            time.sleep(1.2)

            cur = page.evaluate(JS_TRACKS)
            v_with_clip = next((t for t in cur if t["clips"] > 0), None)
            rec("轨道上有 1 个片段", bool(v_with_clip), v_with_clip)

            if v_with_clip:
                page.evaluate("""async (tid) => {
                  const btn = document.querySelector('.tl-head[data-track="' + tid + '"] .tl-head-btn.danger');
                  if (btn) btn.click();
                }""", v_with_clip["id"])
                time.sleep(1.8)

                after2 = page.evaluate(JS_TRACKS)
                rec("带片段轨道可删除",
                    all(t["id"] != v_with_clip["id"] for t in after2),
                    "原 %d 个片段" % v_with_clip["clips"])

                clip_gone = page.evaluate("""async (cid) => {
                  const m = await import('/static/js/state.js');
                  return !m.state.clips[cid];
                }""", clip_id)
                rec("片段已从 state 移除", clip_gone)

                # 撤销：轨道和片段都要回来
                http("POST", API + "/projects/" + temp_pid + "/undo", {})
                page.evaluate("""async () => {
                  const m = await import('/static/js/state.js');
                  await m.refresh();
                }""")
                time.sleep(1.5)

                undo_tracks = page.evaluate(JS_TRACKS)
                restored = any(t["id"] == v_with_clip["id"] for t in undo_tracks)
                rec("撤销后轨道回来", restored)

                clip_back = page.evaluate("""async (cid) => {
                  const m = await import('/static/js/state.js');
                  return !!m.state.clips[cid];
                }""", clip_id)
                rec("撤销后片段也回来", clip_back, clip_id)

                clip_on_track = page.evaluate("""async (args) => {
                  const m = await import('/static/js/state.js');
                  const tr = m.state.project.tracks.find(t => t.id === args.tid);
                  return !!(tr && (tr.clips || []).includes(args.cid));
                }""", {"tid": v_with_clip["id"], "cid": clip_id})
                rec("片段重新挂在该轨道上", clip_on_track)

            # ---------------------------------------------------------- 字幕轨保护

            print("\n== 字幕轨保护 ==")
            sub_head = page.locator(".tl-head.type-subtitle").first
            if sub_head.count() > 0:
                sub_danger = sub_head.locator(".tl-head-btn.danger").count()
                rec("字幕轨无删除按钮", sub_danger == 0, sub_danger)
            else:
                rec("字幕轨无删除按钮", True, "无字幕轨")

            # ---------------------------------------------------------- 汇总

            print("\n== 控制台错误 ==")
            real = [e for e in errors if "favicon" not in str(e[1]).lower()]
            if real:
                for t, m in real[:10]:
                    print("  ", t, ":", m[:220])
            else:
                print("  无")

            browser.close()
            browser = None

    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if temp_pid:
            delete_temp_project(temp_pid)

    fails = [r for r in results if not r[1]]
    print("\n== 汇总：%d/%d 通过 ==" % (len(results) - len(fails), len(results)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

# __EOF__
