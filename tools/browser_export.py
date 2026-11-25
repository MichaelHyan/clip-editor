# -*- coding: utf-8 -*-
"""浏览器实测导出对话框：打开 -> 开始导出 -> 进度推进 -> 完成 -> 下载可用。"""
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
        page.wait_for_selector("#timeline .tl-lane", timeout=8000)

        print("=== 1. 打开导出对话框 ===")
        page.click("#btn-export")
        time.sleep(1.0)
        opened = page.eval_on_selector("#export-modal", "e => e.classList.contains('open')")
        chk("导出对话框打开", opened)

        preset_exists = page.eval_on_selector_all("#export-preset option", "els => els.length")
        chk("预设下拉有选项", preset_exists >= 2, preset_exists)

        print("=== 2. 开始导出 ===")
        page.evaluate("""() => {
          document.getElementById('export-name').value = 'ui_test';
          document.getElementById('export-preset').value = 'h264_web';
          document.getElementById('export-go').click();
        }""")

        # 等待出现进度条
        deadline = time.time() + 10
        got_progress = False
        while time.time() < deadline:
            time.sleep(0.4)
            has_bar = page.evaluate("() => !!document.querySelector('.export-bar')")
            if has_bar:
                got_progress = True
                break
        chk("出现进度条", got_progress)

        print("=== 3. 等待导出完成 ===")
        deadline = time.time() + 120
        status_txt = ""
        done = False
        while time.time() < deadline:
            time.sleep(1.0)
            status_txt = page.evaluate(
                "() => document.querySelector('.export-stat') ? document.querySelector('.export-stat').textContent : ''")
            if "done" in status_txt:
                done = True
                break
            if "failed" in status_txt:
                break
        chk("导出完成", done, status_txt)
        print("   状态:", status_txt)

        btn_text = page.eval_on_selector("#export-go", "e => e.textContent")
        chk("按钮变为下载", btn_text == "下载", btn_text)

        page.screenshot(path="data/_shot_export.png")
        print("截图 -> data/_shot_export.png")

        browser.close()

    print()
    if errors:
        print("失败 %d 项：" % len(errors))
        for e in errors[:20]:
            print("  ", e[:300])
        sys.exit(1)
    print("EXPORT UI ALL PASS")


if __name__ == "__main__":
    main()


# __EOF__