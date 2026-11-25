# -*- coding: utf-8 -*-
"""诊断素材面板：DOM 存在性、尺寸、可见性、JS 错误。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PID = "proj_00c13582b005"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)[:300]))
        page.on("console", lambda m: errs.append("[%s] %s" % (m.type, m.text[:200]))
                if m.type == "error" else None)

        page.goto(BASE + "/?p=" + PID, wait_until="networkidle", timeout=30000)
        time.sleep(3.0)

        st = page.evaluate(r"""() => {
          const q = (s) => document.querySelector(s);
          const rect = (e) => e ? (r => ({
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)
          }))(e.getBoundingClientRect()) : null;

          const panel = q('#assets-panel');
          const body = q('#assets');
          const cards = [...document.querySelectorAll('.asset-card')];
          const empty = q('.assets-empty');
          const mid = q('#mid');

          // 采样第一个卡片各部分的尺寸
          const c0 = cards[0];
          let c0parts = null;
          if (c0) {
            const thumb = c0.querySelector('.asset-thumb');
            const img = c0.querySelector('.asset-thumb img');
            c0parts = {
              card: rect(c0),
              thumb: rect(thumb),
              img: rect(img),
              imgNatural: img ? {w: img.naturalWidth, h: img.naturalHeight,
                                 complete: img.complete} : null,
              display: getComputedStyle(c0).display,
              visibility: getComputedStyle(c0).visibility,
              opacity: getComputedStyle(c0).opacity,
            };
          }

          return {
            midGrid: mid ? getComputedStyle(mid).gridTemplateColumns : null,
            midRect: rect(mid),
            panel: rect(panel),
            panelDisplay: panel ? getComputedStyle(panel).display : null,
            body: rect(body),
            bodyScrollH: body ? body.scrollHeight : null,
            bodyClientH: body ? body.clientHeight : null,
            cardCount: cards.length,
            cardRects: cards.slice(0, 3).map(rect),
            emptyEl: empty ? {text: empty.textContent.slice(0, 40), rect: rect(empty)} : null,
            c0parts,
            assetsCountText: q('#assets-count') ? q('#assets-count').textContent : null,
          };
        }""")

        print("=== DOM 状态 ===")
        print(json.dumps(st, ensure_ascii=False, indent=2))

        print()
        print("=== JS 错误 ===")
        if errs:
            for e in errs[:10]:
                print("  ", e)
        else:
            print("  无")

        page.screenshot(path="D:/git/clip-editor/data/_shot_assets_diag.png")
        print("截图 -> data/_shot_assets_diag.png")
        browser.close()


if __name__ == "__main__":
    main()

# __EOF__