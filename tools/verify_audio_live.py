# -*- coding: utf-8 -*-
"""验证音频参数实时生效：播放中改 gain，GainNode 立即跟随，不重排。"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SRC = "D:/git/clip-editor/data/test_media/sample.mp4"

FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("=== 准备项目 ===")
    pj = http("POST", "/api/projects", {"name": "音频实时测试", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    made = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 0.0})["data"]["clips"]
    cid = made[0]["id"]
    print("  pid=%s cid=%s" % (pid, cid))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(2.5)

        print("=== 1. 调试钩子就位 ===")
        chk("__audioDebug 已挂载", page.evaluate("() => !!window.__audioDebug"))

        print("=== 2. 播放（需用户手势解锁 AudioContext）===")
        page.click("#preview-canvas")
        time.sleep(0.3)
        page.keyboard.press("Space")
        time.sleep(2.0)   # 等解码 + 排程

        st = page.evaluate("() => window.__audioDebug.state()")
        print("  状态:", json.dumps(st, ensure_ascii=False))
        chk("正在播放", st.get("playing") is True, st.get("playing"))
        chk("AudioContext 已运行", st.get("ctxState") == "running", st.get("ctxState"))
        chk("有活动音频源", len(st.get("sources") or []) >= 1, len(st.get("sources") or []))

        if not st.get("sources"):
            print("  无音频源，后续测试跳过")
            browser.close()
            return 1

        g0 = st["sources"][0]["gain"]
        chk("初始 gain ≈ 1.0（0dB）", abs(g0 - 1.0) < 0.05, g0)

        print("=== 3. 改响度为 -6dB，验证 GainNode 实时跟随 ===")
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
             {"patch": {"audio.gain": -6}})
        # clip:changed 会推送到前端 -> updateActiveParams
        time.sleep(1.5)

        st2 = page.evaluate("() => window.__audioDebug.state()")
        g1 = st2["sources"][0]["gain"] if st2.get("sources") else None
        expect = 10 ** (-6 / 20)   # ≈ 0.5012
        print("  gain: %.4f  期望: %.4f" % (g1 or -1, expect))
        chk("-6dB 生效", g1 is not None and abs(g1 - expect) < 0.06, (g1, expect))
        chk("音频源未被重建（仍是同一批）",
            len(st2["sources"]) == len(st["sources"]),
            (len(st["sources"]), len(st2["sources"])))
        chk("播放未中断", st2.get("playing") is True, st2.get("playing"))
        chk("播放时间在推进",
            (st2.get("currentTime") or 0) > (st.get("currentTime") or 0),
            (st.get("currentTime"), st2.get("currentTime")))

        print("=== 4. 改音调 +6 半音 ===")
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
             {"patch": {"audio.pitch": 6}})
        time.sleep(1.2)
        st3 = page.evaluate("() => window.__audioDebug.state()")
        det = st3["sources"][0]["detune"] if st3.get("sources") else None
        print("  detune: %s cents（期望 600）" % det)
        chk("音调 +6 半音 = 600 cents",
            det is not None and abs(det - 600) < 60, det)

        print("=== 5. 改声像 -1（全左）===")
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
             {"patch": {"audio.pan": -1}})
        time.sleep(1.2)
        st4 = page.evaluate("() => window.__audioDebug.state()")
        pan = st4["sources"][0]["pan"] if st4.get("sources") else None
        print("  pan: %s（期望 -1）" % pan)
        chk("声像全左", pan is not None and abs(pan + 1) < 0.1, pan)

        print("=== 6. 恢复 0dB 验证双向生效 ===")
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
             {"patch": {"audio.gain": 0}})
        time.sleep(1.2)
        st5 = page.evaluate("() => window.__audioDebug.state()")
        g2 = st5["sources"][0]["gain"] if st5.get("sources") else None
        chk("恢复 0dB 生效", g2 is not None and abs(g2 - 1.0) < 0.06, g2)

        page.keyboard.press("Space")
        time.sleep(0.4)

        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("AUDIO LIVE ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__