# -*- coding: utf-8 -*-
"""验证 6 项：WSOLA 音高/倍速、轨道头跟随、波形裁剪、视频无波形、片段改名、多工程。"""
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
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


def main():
    print("=== 准备项目 ===")
    pj, _ = http("POST", "/api/projects", {"name": "六项验证", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    made_d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                     {"role": "video", "start": 0.0, "split_av": True})
    made = made_d["data"]["clips"]
    vclip = next(c for c in made if c["media"]["kind"] == "video")
    aclip = next(c for c in made if c["media"]["kind"] == "audio")
    print("  pid=%s" % pid)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:300]))

        page.goto(BASE + "/?p=" + pid, wait_until="networkidle", timeout=30000)
        time.sleep(3.0)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        print("=== 1. WSOLA 变调：音高 +12，时长不变 ===")
        # 音源 10s，+12 半音 = 频率翻倍，但时长必须还是 10s（不是 5s）
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
             {"patch": {"audio.pitch": 12}})
        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        page.click("#preview-canvas")
        time.sleep(0.3)
        page.keyboard.press("Space")
        time.sleep(3.5)   # WSOLA 处理需要时间

        st = page.evaluate("() => window.__audioDebug ? window.__audioDebug.state() : null")
        chk("调试钩子可用", st is not None, st and list(st.keys()))
        if st and st.get("sources"):
            buf_dur = st["sources"][0]["bufDur"]
            rate = st["sources"][0]["rate"]
            print("  处理后 buffer 时长: %ss, rate: %s" % (buf_dur, rate))
            chk("rate 固定为 1.0", abs(rate - 1.0) < 0.01, rate)
            chk("音高 +12 后时长仍 ≈10s（未变短）",
                buf_dur is not None and 9.5 < buf_dur < 10.5, buf_dur)
        else:
            chk("有活动音频源", False, st)

        page.keyboard.press("Space")
        time.sleep(0.5)

        print("=== 2. WSOLA 倍速：speed=2，时长减半，音高不变 ===")
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
             {"patch": {"audio.pitch": 0, "audio.speed": 2.0}})
        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        page.click("#preview-canvas")
        time.sleep(0.3)
        page.keyboard.press("Space")
        time.sleep(3.5)

        st2 = page.evaluate("() => window.__audioDebug ? window.__audioDebug.state() : null")
        if st2 and st2.get("sources"):
            buf_dur2 = st2["sources"][0]["bufDur"]
            print("  speed=2 处理后 buffer 时长: %ss" % buf_dur2)
            chk("倍速 2x 后 buffer 时长 ≈5s",
                buf_dur2 is not None and 4.5 < buf_dur2 < 5.5, buf_dur2)
        else:
            chk("倍速后有音频源", False, st2)
        page.keyboard.press("Space")
        time.sleep(0.5)

        # 还原
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
             {"patch": {"audio.speed": 1.0}})

        print("=== 3. 视频片段不显示波形 ===")
        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        waves = page.evaluate("""(args) => {
          const [vid, aid] = args;
          const vn = document.querySelector('.tl-clip[data-cid="' + vid + '"]');
          const an = document.querySelector('.tl-clip[data-cid="' + aid + '"]');
          return {
            videoHasWave: vn ? !!vn.querySelector('.tl-wave') : null,
            audioHasWave: an ? !!an.querySelector('.tl-wave') : null,
          };
        }""", [vclip["id"], aclip["id"]])
        print("  波形: video=%s audio=%s" % (waves["videoHasWave"], waves["audioHasWave"]))
        chk("视频片段无波形", waves["videoHasWave"] is False, waves["videoHasWave"])
        chk("音频片段有波形", waves["audioHasWave"] is True, waves["audioHasWave"])

        print("=== 4. 轨道头跟随垂直滚动 ===")
        scroll_test = page.evaluate("""() => {
          const sc = document.querySelector('.tl-scroll');
          const hd = document.querySelector('.tl-heads');
          const inner = hd ? hd.querySelector('.tl-heads-inner') : null;
          if (!sc || !hd || !inner) return null;
          // 制造滚动（即使没溢出，代码也会写 transform）
          sc.scrollTop = 120;
          sc.dispatchEvent(new Event('scroll'));
          const tf = inner.style.transform || '';
          let translateY = null;
          const i0 = tf.indexOf('translateY(');
          if (i0 >= 0) {
            const i1 = tf.indexOf('px)', i0);
            if (i1 > i0) translateY = parseFloat(tf.slice(i0 + 11, i1));
          }
          return {
            scrollTop: sc.scrollTop,
            transform: tf,
            translateY: translateY,
          };
        }""")
        print("  滚动测试:", scroll_test)
        # 关键：transform 的 Y 必须等于 -scrollTop
        chk("轨道头 transform 跟随滚动",
            scroll_test and scroll_test["translateY"] is not None
            and abs(scroll_test["translateY"] + scroll_test["scrollTop"]) < 1,
            scroll_test)

        print("=== 5. 片段改名 ===")
        page.evaluate("""(cid) => {
          const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
          const b = n.getBoundingClientRect();
          n.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0,
            clientX: b.x + 30, clientY: b.y + 10}));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""", vclip["id"])
        time.sleep(1.0)

        # 双击标题
        page.dblclick(".insp-title")
        time.sleep(0.5)
        has_input = page.evaluate("() => !!document.querySelector('.insp-title-input')")
        chk("双击后出现输入框", has_input, has_input)

        if has_input:
            page.fill(".insp-title-input", "改名测试镜头")
            page.keyboard.press("Enter")
            time.sleep(1.5)
            d, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, vclip["id"]))
            chk("片段名已落盘", d["data"]["name"] == "改名测试镜头", d["data"]["name"])
            # 时间轴上的标签也应更新
            label = page.evaluate("""(cid) => {
              const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
              const l = n ? n.querySelector('.tl-clip-label') : null;
              return l ? l.textContent : null;
            }""", vclip["id"])
            chk("时间轴标签同步", label == "改名测试镜头", label)

        print("=== 6. 多工程切换 ===")
        # 建第二个项目
        pj2, _ = http("POST", "/api/projects", {"name": "第二项目", "settings": {"fps": 30}})
        pid2 = pj2["data"]["project"]["id"]

        page.click("#proj-switch-btn")
        time.sleep(0.8)
        menu_open = page.evaluate("() => document.querySelector('#proj-menu').classList.contains('open')")
        chk("项目菜单打开", menu_open)

        items = page.eval_on_selector_all(".proj-item .pname", "els => els.map(e => e.textContent)")
        print("  菜单项:", items)
        chk("菜单含两个项目", len(items) >= 2, len(items))
        chk("菜单含第二项目", "第二项目" in items, items)

        current = page.eval_on_selector(".proj-item.current .pname", "e => e.textContent")
        chk("当前项目高亮正确", current == "六项验证", current)

        # 点第二项目
        clicked = page.evaluate("""() => {
          const items = [...document.querySelectorAll('.proj-item')];
          const tgt = items.find(i => i.querySelector('.pname').textContent === '第二项目');
          if (!tgt) return false;
          tgt.click();
          return true;
        }""")
        chk("点击第二项目", clicked)
        time.sleep(3.0)

        # page.url 在直接导航场景下可能滞后，改读 location.href
        url = page.evaluate("() => location.href")
        chk("URL 切换到新项目", pid2 in url, {"want": pid2, "got": url})
        newname = page.eval_on_selector("#proj-name", "e => e.textContent")
        chk("顶栏显示新项目名", newname == "第二项目", newname)
        # 交叉验证：项目设置里的 fps 确实加载了（第二项目 fps=30，不同源项目无法区分，
        # 但至少确认页面数据不是空的）
        meta = page.eval_on_selector("#proj-meta", "e => e.textContent")
        chk("项目元信息已加载", "fps" in (meta or ""), meta)

        page.screenshot(path="data/_shot_fix6.png")
        print("截图 -> data/_shot_fix6.png")

        if errs:
            print("页面错误:", errs[:8])
        browser.close()

    # 清理第二个项目
    http("DELETE", "/api/projects/" + pid2)

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("FIX6 ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__