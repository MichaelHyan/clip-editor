# -*- coding: utf-8 -*-
"""验证素材箱 + 音视频分离 + 裁剪修复 + 音频实时参数。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
COLOR_SRC = "D:/git/clip-editor/data/test_media/sample.mp4"

FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


def main():
    print("=== 1. 建项目 ===")
    d, _ = http("POST", "/api/projects", {"name": "素材测试", "settings": {"fps": 30}})
    pid = d["data"]["project"]["id"]
    print("  pid =", pid)

    print("=== 2. 登记素材（自动拆分音视频卡）===")
    d, code = http("POST", "/api/projects/%s/assets" % pid, {"path": COLOR_SRC})
    chk("登记成功", code == 200 and d.get("ok"), (code, d.get("error")))
    cards = d["data"]["cards"]
    print("  卡片:", [(c["role"], c["label"]) for c in cards])
    chk("视频源拆成 2 张卡", len(cards) == 2, len(cards))
    chk("有视频卡", any(c["role"] == "video" for c in cards))
    chk("有音频卡", any(c["role"] == "audio" for c in cards))
    chk("卡片带时长", all(c.get("duration") for c in cards))

    asset_id = d["data"]["asset"]["id"]

    print("=== 3. 幂等：重复登记不重复添加 ===")
    d2, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": COLOR_SRC})
    chk("标记为已存在", d2["data"].get("existed") is True)
    d3, _ = http("GET", "/api/projects/%s/assets" % pid)
    chk("资产数仍为 1", d3["data"]["count"] == 1, d3["data"]["count"])

    print("=== 4. 分离上时间轴（视频+音频两个 clip）===")
    d, code = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, asset_id),
                   {"role": "video", "start": 2.0, "split_av": True})
    chk("分离添加成功", code == 200 and d.get("ok"), (code, d.get("error")))
    made = d["data"]["clips"]
    chk("生成 2 个 clip", len(made) == 2, len(made))
    kinds = sorted(c["media"]["kind"] for c in made)
    chk("一个是 video 一个是 audio", kinds == ["audio", "video"], kinds)
    tracks = d["data"]["project"]["tracks"]
    vid_ids = {c["id"] for c in made if c["media"]["kind"] == "video"}
    aud_ids = {c["id"] for c in made if c["media"]["kind"] == "audio"}
    vtr = next((t for t in tracks
                if t["type"] == "video" and vid_ids & set(t["clips"])), None)
    atr = next((t for t in tracks
                if t["type"] == "audio" and aud_ids & set(t["clips"])), None)
    chk("视频 clip 在视频轨", vtr is not None)
    chk("音频 clip 在音频轨", atr is not None)
    chk("两者起点一致", abs(made[0]["timeline"]["start"] - made[1]["timeline"]["start"]) < 1e-6,
        [c["timeline"]["start"] for c in made])

    vclip = next(c for c in made if c["media"]["kind"] == "video")
    aclip = next(c for c in made if c["media"]["kind"] == "audio")

    print("=== 5. 裁剪：左边缘只改 src_in，不动 src_out ===")
    orig_vout = vclip["video"]["src_out"]["static"]
    d, _ = http("POST", "/api/projects/%s/clips/%s/trim" % (pid, vclip["id"]),
                {"src_in": 2.0, "src_out": None})
    c = d["data"]
    chk("src_in = 2", abs(c["video"]["src_in"]["static"] - 2.0) < 1e-6, c["video"]["src_in"])
    chk("src_out 保持不变（未被固化）",
        c["video"]["src_out"]["static"] == orig_vout, (orig_vout, c["video"]["src_out"]))
    chk("时长缩短到 8s", abs(c["timeline"]["duration"] - 8.0) < 0.2, c["timeline"]["duration"])

    print("=== 6. 裁剪：右边缘只改 src_out，不动 src_in ===")
    d, _ = http("POST", "/api/projects/%s/clips/%s/trim" % (pid, vclip["id"]),
                {"src_in": None, "src_out": 9.0})
    c = d["data"]
    chk("src_in 仍是 2", abs(c["video"]["src_in"]["static"] - 2.0) < 1e-6, c["video"]["src_in"])
    chk("src_out = 9", abs(c["video"]["src_out"]["static"] - 9.0) < 1e-6, c["video"]["src_out"])
    chk("时长变 7s", abs(c["timeline"]["duration"] - 7.0) < 0.2, c["timeline"]["duration"])

    print("=== 7. 右边缘再拉长（验证之前被固化的 bug 已修）===")
    d, _ = http("POST", "/api/projects/%s/clips/%s/trim" % (pid, vclip["id"]),
                {"src_in": None, "src_out": 10.0})
    c = d["data"]
    chk("src_out 可延长到 10", abs(c["video"]["src_out"]["static"] - 10.0) < 1e-6,
        c["video"]["src_out"])

    print("=== 8. 音频 clip 时长独立 ===")
    d, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, aclip["id"]))
    ac = d["data"]
    chk("音频 clip 时长未受视频裁剪影响",
        abs(ac["timeline"]["duration"] - 10.0) < 0.2, ac["timeline"]["duration"])
    print("  视频 %.2fs | 音频 %.2fs" % (c["timeline"]["duration"], ac["timeline"]["duration"]))

    print("=== 9. 移除素材（不删源文件）===")
    import os
    d, _ = http("DELETE", "/api/projects/%s/assets/%s" % (pid, asset_id))
    chk("移除成功", d.get("ok"), d)
    chk("源文件仍在", os.path.isfile(COLOR_SRC))
    d, _ = http("GET", "/api/projects/%s/assets" % pid)
    chk("素材箱已空", d["data"]["count"] == 0, d["data"]["count"])

    print("=== 10. 浏览器：素材面板渲染 + 拖拽落轨 ===")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.0)

        # 通过素材箱添加
        http("POST", "/api/projects/%s/assets" % pid, {"path": COLOR_SRC})
        page.reload(wait_until="networkidle")
        time.sleep(2.5)

        n_cards = page.eval_on_selector_all(".asset-card", "els => els.length")
        chk("素材面板渲染 2 张卡", n_cards == 2, n_cards)

        roles = page.eval_on_selector_all(".asset-role", "els => els.map(e => e.textContent)")
        chk("卡片带角色标签", "视频" in roles and "音频" in roles, roles)

        has_add_btn = page.eval_on_selector_all(".asset-btn.primary", "els => els.length")
        chk("卡片有添加按钮", has_add_btn >= 2, has_add_btn)

        # 点「+ 时间轴」添加视频卡
        page.evaluate("""() => {
          const card = document.querySelector('.asset-card[data-role="video"]');
          card.querySelector('.asset-btn.primary').click();
        }""")
        time.sleep(2.5)
        n_clips = page.eval_on_selector_all(".tl-clip", "els => els.length")
        chk("点击添加后时间轴有片段", n_clips >= 1, n_clips)

        # 分离添加
        page.evaluate("""() => {
          const card = document.querySelector('.asset-card[data-role="video"]');
          const btns = [...card.querySelectorAll('.asset-btn')];
          const split = btns.find(b => b.textContent === '分离');
          if (split) split.click();
        }""")
        time.sleep(2.5)
        n_clips2 = page.eval_on_selector_all(".tl-clip", "els => els.length")
        chk("分离添加后片段增加 2", n_clips2 >= n_clips + 2, (n_clips, n_clips2))

        page.screenshot(path="data/_shot_assets.png")
        print("截图 -> data/_shot_assets.png")

        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("ASSETS ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__