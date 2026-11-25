# -*- coding: utf-8 -*-
"""隔离变量：crossOrigin='anonymous' 是否导致代理流加载失败。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SRC = "D:/a/standard/zzst.mp4"


def run_case(page, use_co):
    return page.evaluate("""async ({src, useCo}) => {
      const url = '/api/media/stream?path=' + encodeURIComponent(src) + '&proxy=1';
      const v = document.createElement('video');
      v.muted = true; v.preload = 'auto';
      if (useCo) v.crossOrigin = 'anonymous';
      v.src = url;
      const ev = await new Promise(resolve => {
        const to = setTimeout(() => resolve('timeout'), 9000);
        v.addEventListener('loadeddata', () => { clearTimeout(to); resolve('loadeddata'); });
        v.addEventListener('error', () => { clearTimeout(to);
          resolve('error:' + (v.error ? v.error.code + '/' + v.error.message : '?')); });
      });
      // 尝试把一帧画到 canvas，看是否 tainted / 成功
      let drawOk = null, drawErr = null;
      try {
        const cv = document.createElement('canvas');
        cv.width = 100; cv.height = 100;
        cv.getContext('2d').drawImage(v, 0, 0, 100, 100);
        drawOk = true;
      } catch (e) { drawOk = false; drawErr = String(e); }
      return { useCo, ev, readyState: v.readyState, w: v.videoWidth, h: v.videoHeight,
               drawOk, drawErr };
    }""", {"src": SRC, "useCo": use_co})


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        time.sleep(1.0)

        print("=== 无 crossOrigin ===")
        r1 = run_case(page, False)
        print(json.dumps(r1, ensure_ascii=False))

        print("=== 有 crossOrigin='anonymous' ===")
        r2 = run_case(page, True)
        print(json.dumps(r2, ensure_ascii=False))

        print()
        if r1.get("drawOk") and not r2.get("drawOk"):
            print(">>> 结论：crossOrigin 是元凶")
        elif not r1.get("ev", "").startswith("loadeddata") and not r2.get("ev", "").startswith("loadeddata"):
            print(">>> 结论：两种都不加载，代理流对该路径有问题")
        else:
            print(">>> 结论：crossOrigin 不影响加载，问题在 app 内部状态")

        browser.close()


if __name__ == "__main__":
    main()

# __EOF__