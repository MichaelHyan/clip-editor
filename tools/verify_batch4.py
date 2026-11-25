# -*- coding: utf-8 -*-
"""验证四项：素材卡可见 / 全角度擦除 / 项目设置 / 字幕系统。"""
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
    pj, _ = http("POST", "/api/projects", {"name": "四项验证", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    print("  pid =", pid)

    # 默认轨道检查
    tracks = pj["data"]["project"]["tracks"]
    tnames = [t["name"] for t in tracks]
    print("  默认轨道:", tnames)
    chk("有 SUB1 字幕轨", "SUB1" in tnames, tnames)
    chk("不再有 FX1", "FX1" not in tnames, tnames)
    sub_track = next((t for t in tracks if t["type"] == "subtitle"), None)
    chk("字幕轨类型正确", sub_track is not None)

    # 加素材
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]
    made_d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                     {"role": "video", "start": 0.0})
    cid = made_d["data"]["clips"][0]["id"]

    print("=== 1. 项目设置校验 ===")
    d, code = http("PATCH", "/api/projects/" + pid,
                   {"patch": {"settings.width": 99999}})
    chk("宽度超限被拒", code == 400, code)
    d, code = http("PATCH", "/api/projects/" + pid,
                   {"patch": {"settings.fps": 999}})
    chk("帧率超限被拒", code == 400, code)
    d, code = http("PATCH", "/api/projects/" + pid,
                   {"patch": {"settings.width": 1280, "settings.height": 720,
                              "settings.fps": 24}})
    chk("合法设置被接受", code == 200 and d["data"]["settings"]["width"] == 1280,
        (code, d.get("data", {}).get("settings")))

    print("=== 2. 字幕 CRUD ===")
    d, code = http("POST", "/api/projects/%s/subtitles" % pid,
                   {"start": 1.0, "end": 4.0, "text": "第一条字幕"})
    chk("添加字幕", code == 200 and d["data"]["text"] == "第一条字幕", d)
    sid = d["data"]["id"]
    chk("字幕有默认样式", "fontSize" in d["data"]["style"], d["data"].get("style"))

    d, code = http("PATCH", "/api/projects/%s/subtitles/%s" % (pid, sid),
                   {"text": "改后的字幕", "style": {"color": "#ffcc00", "fontSize": 60}})
    chk("修改字幕", code == 200 and d["data"]["text"] == "改后的字幕", d)
    chk("样式已更新", d["data"]["style"]["color"] == "#ffcc00", d["data"].get("style"))

    d, _ = http("GET", "/api/projects/%s/subtitles" % pid)
    chk("字幕列表有 1 条", len(d["data"]) == 1, len(d["data"]))

    print("=== 3. SRT 导出 ===")
    req = urllib.request.Request(BASE + "/api/export/subtitles/%s/srt" % pid)
    with urllib.request.urlopen(req, timeout=20) as r:
        srt = r.read().decode("utf-8")
    print("  SRT 前两行:", srt.split("\r\n")[:2])
    chk("SRT 含时间轴标记", "00:00:01,000 --> 00:00:04,000" in srt, srt[:100])
    chk("SRT 含文本", "改后的字幕" in srt)

    print("=== 4. 全角度擦除（导出链）===")
    for angle in (0, 90, 180, 270):
        http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
             {"patch": {"video.erode": 0.4, "video.erode_angle": angle,
                        "video.erode_softness": 0.02}})
        pc, _ = http("GET", "/api/export/preview-cmd/" + pid)
        f = pc["data"]["filter"]
        has = "geq=" in f and "a=" in f
        chk("角度 %d 有 geq" % angle, has, f[-200:] if not has else "")

    # 复原擦除
    http("PATCH", "/api/projects/%s/clips/%s" % (pid, cid),
         {"patch": {"video.erode": 0.0}})

    print("=== 5. 浏览器验证 ===")
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
        page.wait_for_selector("#timeline .tl-lane", timeout=10000)

        # 素材卡高度（问题 1 修复验证）
        card_h = page.evaluate("""() => {
          const c = document.querySelector('.asset-card');
          if (!c) return null;
          const info = c.querySelector('.asset-info');
          const del = c.querySelector('.asset-btn.danger');
          return {
            h: Math.round(c.getBoundingClientRect().height),
            infoVisible: info ? info.getBoundingClientRect().height > 0 : false,
            delText: del ? del.textContent : null,
          };
        }""")
        print("  素材卡:", card_h)
        chk("素材卡高度 > 120px（信息区可见）", card_h and card_h["h"] > 120, card_h)
        chk("移除按钮可见且有文字", card_h and card_h["delText"] == "移除", card_h)
        chk("素材信息区可见", card_h and card_h["infoVisible"], card_h)

        # 字幕轨 + 加字幕
        lane_types = page.eval_on_selector_all(".tl-lane", "els => els.map(e => e.dataset.type)")
        print("  轨道类型:", lane_types)
        chk("时间轴有 subtitle 轨", "subtitle" in lane_types, lane_types)

        # 点 SUB 轨的 + 按钮
        clicked = page.evaluate("""() => {
          const heads = [...document.querySelectorAll('.tl-head')];
          const subHead = heads.find(h => h.dataset.track);
          // 找 type-subtitle 的头
          const sh = document.querySelector('.tl-head.type-subtitle');
          if (!sh) return false;
          const btn = sh.querySelector('.tl-head-btn');
          if (!btn) return false;
          btn.click();
          return true;
        }""")
        chk("点击 + 字幕按钮", clicked)
        time.sleep(2.0)

        sub_blocks = page.eval_on_selector_all(".tl-sub", "els => els.length")
        chk("时间轴出现字幕块", sub_blocks >= 1, sub_blocks)

        # 检查器显示字幕编辑
        insp_title = page.evaluate("""() => {
          const t = document.querySelector('.insp-title');
          return t ? t.textContent : null;
        }""")
        chk("检查器切到字幕编辑", insp_title == "字幕", insp_title)

        has_textarea = page.evaluate("() => !!document.querySelector('.insp-textarea')")
        chk("有文本输入框", has_textarea)

        # 字幕在预览上渲染
        page.evaluate("""() => {
          const r = document.querySelector('.tl-ruler').getBoundingClientRect();
          document.querySelector('.tl-ruler').dispatchEvent(new PointerEvent(
            'pointerdown', {clientX: r.left + 150, clientY: r.top + 8,
                            bubbles: true, button: 0}));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        }""")
        time.sleep(2.0)
        # 字幕应在画布下半部分留下白色像素
        px = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          const g = cv.getContext('2d');
          const W = cv.width, H = cv.height;
          // 采样下半部分（字幕默认 y=0.86）
          const y0 = Math.floor(H * 0.78);
          const d = g.getImageData(0, y0, W, H - y0).data;
          let white = 0;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i] > 200 && d[i+1] > 200 && d[i+2] > 200) white++;
          }
          return {white, total: d.length / 4};
        }""")
        print("  字幕区白色像素:", px)
        chk("预览上有字幕文字（白色像素）", px["white"] > 200, px)

        # 项目设置对话框
        page.click("#btn-settings")
        time.sleep(1.0)
        opened = page.evaluate("() => document.getElementById('settings-modal').classList.contains('open')")
        chk("设置对话框打开", opened)

        fields = page.evaluate("""() => ({
          hasW: !!document.getElementById('set-w'),
          hasH: !!document.getElementById('set-h'),
          hasFps: !!document.getElementById('set-fps'),
          hasSr: !!document.getElementById('set-sr'),
          hasBg: !!document.getElementById('set-bg'),
          wVal: document.getElementById('set-w')?.value,
          hVal: document.getElementById('set-h')?.value,
        })""")
        print("  设置字段:", fields)
        chk("有宽高帧率采样率背景字段",
            fields["hasW"] and fields["hasH"] and fields["hasFps"]
            and fields["hasSr"] and fields["hasBg"], fields)
        chk("宽高回显当前值", fields["wVal"] == "1280" and fields["hVal"] == "720", fields)

        # 改设置为 1920x1080 并应用
        page.fill("#set-w", "1920")
        page.fill("#set-h", "1080")
        page.fill("#set-fps", "60")
        page.click("#settings-save")
        time.sleep(2.0)

        d, _ = http("GET", "/api/projects/" + pid)
        st = d["data"]["project"]["settings"]
        chk("设置已落盘", st["width"] == 1920 and st["height"] == 1080 and st["fps"] == 60, st)

        # 画布尺寸跟随
        cv_size = page.evaluate("""() => {
          const cv = document.getElementById('preview-canvas');
          return {w: cv.width, h: cv.height};
        }""")
        chk("预览画布跟随设置", cv_size["w"] == 1920 and cv_size["h"] == 1080, cv_size)

        page.screenshot(path="data/_shot_batch4.png")
        print("截图 -> data/_shot_batch4.png")

        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    # 清理
    http("DELETE", "/api/projects/" + pid)

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("BATCH4 ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__