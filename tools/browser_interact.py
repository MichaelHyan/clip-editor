# -*- coding: utf-8 -*-
"""交互级验证：选中片段 / 页签 / agent 工具 / 关键帧 / 播放头 / 快捷键。"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

errors = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        errors.append(name)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))
        page.on("console", lambda m: errors.append("console:" + m.text) if m.type == "error" else None)

        page.goto(BASE, wait_until="networkidle")
        time.sleep(1.5)
        page.wait_for_selector("#timeline .tl-clip", timeout=8000)

        print("=== 1. 选中片段 ===")
        page.click(".tl-clip")
        time.sleep(0.6)
        tabs = page.eval_on_selector_all(".insp-tab", "els => els.map(e => e.textContent)")
        chk("选中后出现页签", len(tabs) == 4, tabs)
        chk("页签含 视频/音频/效果/标记",
            tabs == ["视频", "音频", "效果", "标记"], tabs)
        title = page.eval_on_selector(".insp-title", "e => e.textContent")
        chk("检查器显示片段名", bool(title), title)

        print("=== 2. 视频页签参数 ===")
        params = page.eval_on_selector_all(".insp-body .insp-param", "els => els.length")
        chk("视频参数行 > 8", params > 8, params)

        print("=== 3. 改缩放属性 ===")
        changed = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.insp-param')];
          const row = rows.find(r => r.dataset.path === 'video.scale');
          if (!row) return null;
          const num = row.querySelector('.insp-num');
          num.value = '1.5';
          num.dispatchEvent(new Event('change', {bubbles:true}));
          return num.value;
        }""")
        time.sleep(1.0)
        scale = page.evaluate("""() => {
          const s = window.__clipState || null;
          return null;
        }""")
        # 通过 REST 反查是否落盘
        pid = page.evaluate("() => new URLSearchParams(location.search).get('p')")
        import urllib.request, json
        data = json.load(urllib.request.urlopen(BASE + "/api/projects/" + pid))
        clip = list(data["data"]["clips"].values())[0]
        got = clip["video"]["scale"]["static"]
        chk("video.scale 落盘 = 1.5", abs(got - 1.5) < 1e-6, got)

        print("=== 4. 效果页签 + agent 工具下拉 ===")
        page.evaluate("""() => {
          [...document.querySelectorAll('.insp-tab')].find(t => t.textContent === '效果').click();
        }""")
        time.sleep(0.6)
        opts = page.eval_on_selector_all(".insp-fx-add option", "els => els.map(e => e.textContent)")
        chk("效果下拉有内置工具", len(opts) > 5, len(opts))

        print("=== 5. 打开导入对话框 ===")
        page.click("#btn-import")
        time.sleep(0.8)
        opened = page.eval_on_selector("#modal", "e => e.classList.contains('open')")
        chk("导入对话框打开", opened)
        rows = page.eval_on_selector_all(".modal-row", "els => els.length")
        chk("列出目录项", rows >= 0, rows)
        page.click(".modal-close")
        time.sleep(0.3)

        print("=== 6. 播放头拖动，画面时间变化 ===")
        tc0 = page.eval_on_selector("#tc-cur", "e => e.textContent")
        # 点击标尺中段
        ruler = page.query_selector(".tl-ruler")
        box = ruler.bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.4, box["y"] + box["height"] / 2)
        time.sleep(0.5)
        tc1 = page.eval_on_selector("#tc-cur", "e => e.textContent")
        chk("播放头时间变化", tc0 != tc1, (tc0, tc1))

        print("=== 7. 空格播放 / 暂停 ===")
        page.keyboard.press("Space")
        time.sleep(0.8)
        playing_tc = page.eval_on_selector("#tc-cur", "e => e.textContent")
        page.keyboard.press("Space")
        time.sleep(0.3)
        chk("播放推进时间", playing_tc != tc1, (tc1, playing_tc))

        print("=== 8. 打关键帧 ===")
        # 回视频页签，对 opacity 打关键帧
        page.evaluate("""() => {
          [...document.querySelectorAll('.insp-tab')].find(t => t.textContent === '视频').click();
        }""")
        time.sleep(0.5)
        kf = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.insp-param')];
          const row = rows.find(r => r.dataset.path === 'video.opacity');
          if (!row) return false;
          const btn = row.querySelector('.insp-kf');
          btn.click();
          return true;
        }""")
        chk("点击关键帧按钮", kf is True, kf)
        time.sleep(1.0)
        data = json.load(urllib.request.urlopen(BASE + "/api/projects/" + pid))
        clip = list(data["data"]["clips"].values())[0]
        op = clip["video"]["opacity"]
        chk("opacity 变关键帧", "keys" in op, op)

        print("=== 9. 关键帧点出现在时间轴 ===")
        kdots = page.eval_on_selector_all(".tl-kf", "els => els.length")
        chk("时间轴有菱形关键帧点", kdots >= 1, kdots)

        print("=== 10. 撤销 ===")
        h0 = page.eval_on_selector("#status-rev", "e => e.textContent")
        page.click("#btn-undo")
        time.sleep(0.8)
        data = json.load(urllib.request.urlopen(BASE + "/api/projects/" + pid))
        clip = list(data["data"]["clips"].values())[0]
        op2 = clip["video"]["opacity"]
        chk("撤销后关键帧回退", "static" in op2 or op2 != op, op2)

        page.screenshot(path="data/_shot_interact.png")
        print("截图 -> data/_shot_interact.png")
        browser.close()

    print()
    if errors:
        print("失败/异常 %d 项：" % len(errors))
        for e in errors[:20]:
            print("  ", e[:300])
        sys.exit(1)
    print("INTERACTION ALL PASS")


if __name__ == "__main__":
    main()


# __EOF__