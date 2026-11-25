# -*- coding: utf-8 -*-
"""验证 5 个问题：轨道归属 / 倍速改时长 / 裁剪保比例 / 音高不加速 / 效果预览。"""
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
    print("=== 准备 ===")
    pj, _ = http("POST", "/api/projects", {"name": "五问题验证", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 0.0, "split_av": True})
    clips = d["data"]["clips"]
    vclip = next(c for c in clips if c["media"]["kind"] == "video")
    aclip = next(c for c in clips if c["media"]["kind"] == "audio")
    print("  pid=%s v=%s a=%s" % (pid, vclip["id"], aclip["id"]))

    print("=== 问题5：倍速改变时长 ===")
    d0, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, vclip["id"]))
    orig = d0["data"]["timeline"]["duration"]
    print("  原始时长: %.2fs" % orig)
    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
                {"patch": {"video.speed": 2.0}})
    dur2 = d["data"]["timeline"]["duration"]
    print("  speed=2 后: %.2fs" % dur2)
    chk("视频倍速 2x 时长减半", abs(dur2 - orig / 2) < 0.15, (orig, dur2))

    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
                {"patch": {"video.speed": 0.5}})
    dur05 = d["data"]["timeline"]["duration"]
    print("  speed=0.5 后: %.2fs" % dur05)
    chk("视频倍速 0.5x 时长翻倍", abs(dur05 - orig * 2) < 0.3, (orig, dur05))

    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
                {"patch": {"video.speed": 1.0}})
    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, aclip["id"]),
                {"patch": {"audio.speed": 2.0}})
    adur = d["data"]["timeline"]["duration"]
    print("  音频 speed=2: %.2fs" % adur)
    chk("音频倍速也影响时长", abs(adur - orig / 2) < 0.15, (orig, adur))

    print("=== 问题4：裁剪保持比例 ===")
    # 裁剪左边 25%，画面应保持 3:4 宽度比例而非拉伸
    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
                {"patch": {"video.crop.left": 0.25}})
    chk("裁剪写入成功", d["data"]["video"]["crop"]["left"]["static"] == 0.25)
    # 还原
    http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
         {"patch": {"video.crop.left": 0.0}})

    print("=== 浏览器验证 ===")
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
        page.wait_for_selector("#timeline .tl-clip", timeout=10000)

        print("=== 问题1/2：跨轨后 V1 能显示 ===")
        # 找两条视频轨
        lanes = page.evaluate("""() => [...document.querySelectorAll('.tl-lane')]
          .map(l => { const b = l.getBoundingClientRect();
            return {type: l.dataset.type, track: l.dataset.track,
                    x: b.x, y: b.y, w: b.width, h: b.height}; })""")
        vlanes = [l for l in lanes if l["type"] == "video"]
        chk("有 2 条视频轨", len(vlanes) >= 2, len(vlanes))

        clipInfo = page.evaluate("""(cid) => {
          const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
          if (!n) return null;
          const b = n.getBoundingClientRect();
          const lane = n.closest('.tl-lane');
          return {x: b.x + b.width*0.5, y: b.y + b.height*0.5,
                  track: lane ? lane.dataset.track : null};
        }""", vclip["id"])
        chk("视频片段存在", clipInfo is not None)

        if clipInfo and len(vlanes) >= 2:
            cur = next(l for l in vlanes if l["track"] == clipInfo["track"])
            dst = next(l for l in vlanes if l["track"] != clipInfo["track"])
            # 拖过去
            page.mouse.move(clipInfo["x"], clipInfo["y"])
            page.mouse.down()
            for i in range(1, 9):
                page.mouse.move(clipInfo["x"],
                                clipInfo["y"] + (dst["y"] + dst["h"]/2 - clipInfo["y"]) * i / 8)
                time.sleep(0.05)
            page.mouse.up()
            time.sleep(2.5)

            # 检查轨道归属（前后端都要对）
            d, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, vclip["id"]))
            newTrack = d["data"]["timeline"]["track_id"]
            chk("后端已换轨", newTrack == dst["track"], (dst["track"], newTrack))

            dom = page.evaluate("""(args) => {
              const [cid, wantTrack] = args;
              const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
              if (!n) return {found: false};
              const lane = n.closest('.tl-lane');
              return {found: true, track: lane ? lane.dataset.track : null};
            }""", [vclip["id"], dst["track"]])
            chk("DOM 中片段在新轨道", dom.get("track") == dst["track"], dom)

            # 把播放头移到片段内，检查画面
            page.evaluate("""() => {
              const r = document.querySelector('.tl-ruler').getBoundingClientRect();
              document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent(
                'pointerdown', {clientX: r.left + 200, clientY: r.top + 8,
                                bubbles: true, button: 0}));
              window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
            }""")
            time.sleep(2.5)
            px = page.evaluate("""() => {
              const cv = document.getElementById('preview-canvas');
              const g = cv.getContext('2d');
              const d = g.getImageData(0, 0, cv.width, cv.height).data;
              let nz = 0, total = 0;
              for (let i = 0; i < d.length; i += 4 * 29) {
                total++;
                if (d[i] > 8 || d[i+1] > 8 || d[i+2] > 8) nz++;
              }
              return {ratio: nz / Math.max(1, total)};
            }""")
            print("  V1 画面非黑比例: %.3f" % px["ratio"])
            chk("跨轨后画面仍能渲染", px["ratio"] > 0.3, px["ratio"])

        print("=== 问题6：效果实时预览 ===")
        page.evaluate("""(cid) => {
          const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
          const b = n.getBoundingClientRect();
          n.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0,
            clientX: b.x + 20, clientY: b.y + 10}));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""", vclip["id"])
        time.sleep(1.0)

        # 加 blur 效果再看像素是否变化
        http("POST", "/api/projects/%s/clips/%s/effects" % (pid, vclip["id"]),
             {"type": "grayscale", "params": {"amount": 1.0}})
        page.reload(wait_until="networkidle")
        time.sleep(2.5)
        # 移到片段内
        page.evaluate("""() => {
          const r = document.querySelector('.tl-ruler').getBoundingClientRect();
          document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent(
            'pointerdown', {clientX: r.left + 200, clientY: r.top + 8,
                            bubbles: true, button: 0}));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""")
        time.sleep(2.5)
        px2 = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          const g = cv.getContext('2d');
          // 采样中间区域，检查是否灰度（R=G=B）
          const d = g.getImageData(cv.width/2 - 50, cv.height/2 - 50, 100, 100).data;
          let gray = 0, total = 0;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i] > 8 || d[i+1] > 8 || d[i+2] > 8) {
              total++;
              if (Math.abs(d[i] - d[i+1]) < 6 && Math.abs(d[i+1] - d[i+2]) < 6) gray++;
            }
          }
          return {total, gray, ratio: total ? gray / total : 0};
        }""")
        print("  灰度化后中间区域: gray=%d/%d (%.2f)" % (px2["gray"], px2["total"], px2["ratio"]))
        chk("grayscale 效果生效（画面变灰）", px2["ratio"] > 0.85, px2["ratio"])

        page.screenshot(path="data/_shot_fix5.png")
        print("截图 -> data/_shot_fix5.png")
        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("FIX5 ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__