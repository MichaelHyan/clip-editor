# -*- coding: utf-8 -*-
"""验证：跨轨拖拽 + 视频片段禁用音频。

真实的鼠标拖拽事件序列：pointerdown -> 多次 pointermove（带位移） -> pointerup。
用 Playwright 的 mouse API 保证 elementFromPoint 能得到真实坐标结果。
"""
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
    pj, _ = http("POST", "/api/projects", {"name": "跨轨测试", "settings": {"fps": 30}})
    pid = pj["data"]["project"]["id"]
    res, _ = http("POST", "/api/projects/%s/assets" % pid, {"path": SRC})
    aid = res["data"]["asset"]["id"]

    # 分离上轨：视频 -> V2，音频 -> A1
    d, _ = http("POST", "/api/projects/%s/assets/%s/to-timeline" % (pid, aid),
                {"role": "video", "start": 0.0, "split_av": True})
    clips = d["data"]["clips"]
    vclip = next(c for c in clips if c["media"]["kind"] == "video")
    aclip = next(c for c in clips if c["media"]["kind"] == "audio")

    print("=== 1. 视频片段 audio.enabled 必须为 false ===")
    chk("视频 clip audio.enabled=false", vclip["audio"]["enabled"] is False,
        vclip["audio"]["enabled"])
    chk("视频 clip video.enabled=true", vclip["video"]["enabled"] is True)
    chk("音频 clip video.enabled=false", aclip["video"]["enabled"] is False)
    chk("音频 clip audio.enabled=true", aclip["audio"]["enabled"] is True)

    print("=== 2. 旧数据兼容：显式开音频也允许（不强制改历史）===")
    d, _ = http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
                {"patch": {"audio.enabled": True}})
    chk("PATCH 可显式开启（接口不拦）", d["data"]["audio"]["enabled"] is True)
    # 复原
    http("PATCH", "/api/projects/%s/clips/%s" % (pid, vclip["id"]),
         {"patch": {"audio.enabled": False}})

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

        # 拿到轨道位置
        info = page.evaluate("""(args) => {
          const [vid, aid] = args;
          const vn = document.querySelector('.tl-clip[data-cid="' + vid + '"]');
          const an = document.querySelector('.tl-clip[data-cid="' + aid + '"]');
          const lanes = [...document.querySelectorAll('.tl-lane')].map(l => ({
            type: l.dataset.type, track: l.dataset.track,
            rect: (r => ({x:r.x, y:r.y, w:r.width, h:r.height}))(l.getBoundingClientRect())
          }));
          const rv = vn ? vn.getBoundingClientRect() : null;
          const ra = an ? an.getBoundingClientRect() : null;
          return {
            videoClip: rv ? {x:rv.x, y:rv.y, w:rv.width, h:rv.height} : null,
            audioClip: ra ? {x:ra.x, y:ra.y, w:ra.width, h:ra.height} : null,
            lanes,
            vtrack: vn ? vn.closest('.tl-lane').dataset.track : null,
            atrack: an ? an.closest('.tl-lane').dataset.track : null,
          };
        }""", [vclip["id"], aclip["id"]])
        print("  初始: video@%s audio@%s" % (info["vtrack"], info["atrack"]))
        print("  轨道:", [(l["type"], l["track"]) for l in info["lanes"]])

        # 找视频轨（有两条 video 轨 V2/V1）
        v_lanes = [l for l in info["lanes"] if l["type"] == "video"]
        chk("有 2 条视频轨", len(v_lanes) >= 2, len(v_lanes))

        vc = info["videoClip"]
        chk("视频片段可见", vc is not None)

        if vc and len(v_lanes) >= 2:
            # 目标：另一条视频轨的 y
            cur_lane = next(l for l in v_lanes if l["track"] == info["vtrack"])
            other_lane = next((l for l in v_lanes if l["track"] != info["vtrack"]), None)
            if other_lane:
                startX = vc["x"] + vc["w"] / 2
                startY = vc["y"] + vc["h"] / 2
                endY = other_lane["rect"]["y"] + other_lane["rect"]["h"] / 2
                print("=== 3. 把视频片段从 %s 拖到 %s ===" % (info["vtrack"], other_lane["track"]))

                page.mouse.move(startX, startY)
                page.mouse.down()
                # 分步移动，模拟真实拖拽
                steps = 8
                for i in range(1, steps + 1):
                    page.mouse.move(startX + 20 * i / steps,
                                    startY + (endY - startY) * i / steps)
                    time.sleep(0.04)
                time.sleep(0.2)
                hovered = page.evaluate("() => !!document.querySelector('.tl-lane.drop-target')")
                chk("拖拽中有目标轨道高亮", hovered, hovered)
                page.mouse.up()
                time.sleep(2.5)

                d, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, vclip["id"]))
                new_track = d["data"]["timeline"]["track_id"]
                print("  拖后所在轨道:", new_track)
                chk("视频片段换了轨道", new_track != info["vtrack"], (info["vtrack"], new_track))
                chk("落到目标视频轨", new_track == other_lane["track"],
                    (other_lane["track"], new_track))

        # 音频跨轨（只有一条音频轨，测不能落到视频轨）
        print("=== 4. 音频片段拖到视频轨应被拒绝 ===")
        page.reload(wait_until="networkidle")
        time.sleep(2.0)
        info2 = page.evaluate("""(cid) => {
          const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
          const r = n.getBoundingClientRect();
          const vl = [...document.querySelectorAll('.tl-lane[data-type="video"]')]
            .map(l => (b => ({track: l.dataset.track, y: b.y, h: b.height}))(l.getBoundingClientRect()));
          return {clip: {x:r.x,y:r.y,w:r.width,h:r.height}, vlanes: vl};
        }""", aclip["id"])
        ac = info2["clip"]
        if ac and info2["vlanes"]:
            tx = ac["x"] + ac["w"] / 2
            ty = ac["y"] + ac["h"] / 2
            target = info2["vlanes"][0]
            page.mouse.move(tx, ty)
            page.mouse.down()
            for i in range(1, 9):
                page.mouse.move(tx, ty + (target["y"] + target["h"] / 2 - ty) * i / 8)
                time.sleep(0.04)
            time.sleep(0.2)
            no_hl = page.evaluate(
                "() => !document.querySelector('.tl-lane[data-type=\\\"video\\\"].drop-target')")
            chk("音频拖到视频轨不高亮", no_hl, no_hl)
            page.mouse.up()
            time.sleep(2.0)
            d, _ = http("GET", "/api/projects/%s/clips/%s" % (pid, aclip["id"]))
            chk("音频仍在原音频轨",
                d["data"]["timeline"]["track_id"] == aclip["timeline"]["track_id"],
                d["data"]["timeline"]["track_id"])

        print("=== 5. 检查器音频页签显示提示 ===")
        page.evaluate("""(cid) => {
          const n = document.querySelector('.tl-clip[data-cid="' + cid + '"]');
          n.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0,
            clientX: n.getBoundingClientRect().x + 20,
            clientY: n.getBoundingClientRect().y + 10}));
          window.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
        }""", vclip["id"])
        time.sleep(1.0)
        page.evaluate("""() => {
          const t = [...document.querySelectorAll('.insp-tab')].find(x => x.textContent === '音频');
          if (t) t.click();
        }""")
        time.sleep(0.8)
        note = page.eval_on_selector_all(".insp-note", "els => els.map(e => e.textContent)")
        chk("视频片段音频页显示提示", len(note) >= 1, note)
        if note:
            chk("提示说明音频已禁用", "禁用" in note[0], note[0][:60])

        page.screenshot(path="data/_shot_cross.png")
        print("截图 -> data/_shot_cross.png")
        if errs:
            print("页面错误:", errs[:5])
        browser.close()

    print()
    if FAIL:
        print("失败 %d 项: %s" % (len(FAIL), FAIL))
        return 1
    print("CROSS TRACK ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# __EOF__