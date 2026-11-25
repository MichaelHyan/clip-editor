# -*- coding: utf-8 -*-
"""像素级验证：打开含 mpeg4 源的项目，确认 Canvas 真有画面内容（非全黑）。"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PROJECT = "proj_00c13582b005"
FAIL = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))

        page.goto(BASE + "/?p=" + PROJECT, wait_until="networkidle", timeout=30000)
        time.sleep(2.0)
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        print("=== 1. 片段已加载 ===")
        n = page.eval_on_selector_all(".tl-clip", "els => els.length")
        chk("时间轴有片段", n >= 1, n)

        print("=== 2. 播放头移到片段内部 ===")
        # 点标尺 1/4 处，确保落在片段内
        ruler = page.query_selector(".tl-ruler")
        box = ruler.bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
        time.sleep(1.5)

        tc = page.eval_on_selector("#tc-cur", "e => e.textContent")
        print("   播放头:", tc)

        print("=== 3. video 元素状态 ===")
        vinfo = page.evaluate("""() => {
          const vs = [...document.querySelectorAll('video')];
          return vs.map(v => ({
            src: (v.currentSrc || v.src || '').slice(-60),
            readyState: v.readyState,
            w: v.videoWidth,
            h: v.videoHeight,
            error: v.error ? v.error.code : null,
          }));
        }""")
        print("   video 元素:", vinfo)
        chk("存在 video 元素", len(vinfo) >= 1, len(vinfo))
        if vinfo:
            v0 = vinfo[0]
            chk("video 已解码(readyState>=2)", v0["readyState"] >= 2, v0["readyState"])
            chk("video 有尺寸", v0["w"] > 0 and v0["h"] > 0, (v0["w"], v0["h"]))
            chk("video 无错误", v0["error"] is None, v0["error"])
            chk("用的是代理", "proxy=1" in v0["src"] or ".mp4" in v0["src"], v0["src"])

        print("=== 4. Canvas 像素检测（核心）===")
        stats = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          if (!cv) return null;
          const g = cv.getContext('2d');
          // 取中部区域采样，避开边缘
          const w = cv.width, h = cv.height;
          const x = Math.floor(w * 0.2), y = Math.floor(h * 0.2);
          const sw = Math.floor(w * 0.6), sh = Math.floor(h * 0.6);
          const img = g.getImageData(x, y, sw, sh).data;
          let nonzero = 0, total = 0;
          const colors = new Set();
          for (let i = 0; i < img.length; i += 4 * 37) {  // 抽样
            const r = img[i], gg = img[i+1], b = img[i+2];
            total++;
            if (r > 8 || gg > 8 || b > 8) nonzero++;
            colors.add(r + ',' + gg + ',' + b);
          }
          return { total, nonzero, ratio: nonzero / Math.max(1,total), uniqueColors: colors.size,
                   cw: w, ch: h };
        }""")
        print("   像素统计:", stats)
        chk("canvas 有非黑像素", stats and stats["ratio"] > 0.3, stats and stats["ratio"])
        chk("画面有色彩层次(非纯色)", stats and stats["uniqueColors"] > 20, stats and stats["uniqueColors"])

        print("=== 5. 连续两帧有变化（视频在动）===")
        first = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          const g = cv.getContext('2d');
          return Array.from(g.getImageData(0, 0, 200, 200).data.slice(0, 4000)).join(',');
        }""")
        time.sleep(1.2)
        second = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          const g = cv.getContext('2d');
          return Array.from(g.getImageData(0, 0, 200, 200).data.slice(0, 4000)).join(',');
        }""")
        chk("画面随时间变化", first != second)

        page.screenshot(path="data/_shot_preview_pixel.png")
        print("截图 -> data/_shot_preview_pixel.png")

        if errors:
            print("页面错误:", errors[:5])

        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        sys.exit(1)
    print("PREVIEW PIXEL ALL PASS")


if __name__ == "__main__":
    main()

# __EOF__