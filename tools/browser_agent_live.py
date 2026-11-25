# -*- coding: utf-8 -*-
"""核心闭环验证：agent 注册工具 -> 前端自动长出控件 -> 应用到片段 -> 改参落盘。

浏览器保持打开，用独立 HTTP 请求模拟 agent，验证 SocketIO 实时推送。
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
TOOL_ID = "agent_zoom_pulse"

errors = []


def chk(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        errors.append(name)


def http(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    try:
        http("DELETE", "/api/agent/tools/" + TOOL_ID)
    except Exception:
        pass

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required",
                                          "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))

        page.goto(BASE, wait_until="networkidle")
        time.sleep(1.5)
        page.wait_for_selector("#timeline .tl-clip", timeout=8000)

        pid = page.evaluate("() => new URLSearchParams(location.search).get('p')")
        print("项目:", pid)

        page.click(".tl-clip")
        time.sleep(0.5)
        page.evaluate("""() => {
          [...document.querySelectorAll('.insp-tab')].find(t => t.textContent === '效果').click();
        }""")
        time.sleep(0.6)

        before = page.eval_on_selector_all(".insp-fx-add option", "els => els.map(e => e.textContent)")
        chk("注册前下拉不含该工具",
            not any(TOOL_ID in (o or "") for o in before), before)

        print("=== agent 注册新工具（外部 HTTP，模拟另一个客户端）===")
        tool = {
            "id": TOOL_ID,
            "name": "变焦脉冲",
            "category": "stylize",
            "scope": "video",
            "params": {
                "amount": {"type": "number", "default": 0.2, "min": 0, "max": 2,
                           "step": 0.01, "keyframeable": True},
                "axis": {"type": "select", "default": "x", "keyframeable": False,
                         "options": [{"value": "x", "label": "横向"},
                                     {"value": "y", "label": "纵向"}]},
            },
            "preview": {"kind": "css", "template": "scale(1+{amount})"},
            "export": {"filter": "zoompan", "template": "zoompan=z='1+{amount}'"},
            "ui": {"panel": "effects", "group": "agent 实时注入", "order": 5,
                   "icon": "zoom", "help": "测试用动态工具"},
        }
        reg = http("POST", "/api/agent/tools", tool)
        chk("工具注册成功", reg.get("ok") and reg["data"]["id"] == TOOL_ID, reg)

        deadline = time.time() + 8
        appeared = False
        while time.time() < deadline:
            time.sleep(0.5)
            opts = page.eval_on_selector_all(".insp-fx-add option", "els => els.map(e => e.textContent)")
            if any((o or "").strip() == "变焦脉冲" for o in opts):
                appeared = True
                break
        chk("前端下拉自动出现新工具（无需刷新）", appeared)

        group_ok = page.evaluate("""() => {
          const gs = [...document.querySelectorAll('.insp-fx-add optgroup')].map(g => g.label);
          return gs.includes('agent 实时注入');
        }""")
        chk("ui.group 分组透传", group_ok)

        print("=== 通过 UI 应用该工具 ===")
        page.select_option(".insp-fx-add select", label="变焦脉冲")
        time.sleep(1.2)

        fx_count = page.eval_on_selector_all(".insp-fx", "els => els.length")
        chk("效果实例已挂上", fx_count >= 1, fx_count)

        names = page.eval_on_selector_all(".insp-fx-name", "els => els.map(e => e.textContent)")
        chk("实例名为工具名", "变焦脉冲" in names, names)

        ctrl = page.evaluate("""() => {
          const box = [...document.querySelectorAll('.insp-fx')]
            .find(b => b.querySelector('.insp-fx-name')?.textContent === '变焦脉冲');
          if (!box) return null;
          return {
            ranges: box.querySelectorAll('input[type=range]').length,
            selects: box.querySelectorAll('select').length,
            kf: box.querySelectorAll('.insp-kf').length,
          };
        }""")
        chk("自动生成 number 控件", ctrl and ctrl["ranges"] == 1, ctrl)
        chk("自动生成 select 控件", ctrl and ctrl["selects"] == 1, ctrl)
        chk("keyframeable 参数有关键帧按钮", ctrl and ctrl["kf"] == 1, ctrl)

        print("=== UI 改参数 -> 落盘 ===")
        page.evaluate("""() => {
          const box = [...document.querySelectorAll('.insp-fx')]
            .find(b => b.querySelector('.insp-fx-name')?.textContent === '变焦脉冲');
          const num = box.querySelector('input[type=number]');
          num.value = '0.75';
          num.dispatchEvent(new Event('change', {bubbles:true}));
        }""")
        time.sleep(1.0)
        data = http("GET", "/api/projects/" + pid)
        clip = list(data["data"]["clips"].values())[0]
        fx = next((f for f in clip["effects"] if f["type"] == TOOL_ID), None)
        chk("参数已落盘 amount=0.75",
            fx and abs(fx["params"]["amount"]["static"] - 0.75) < 1e-6,
            fx and fx.get("params"))

        print("=== agent 改定义 -> 前端控件同步更新 ===")
        tool2 = dict(tool)
        tool2["name"] = "变焦脉冲 v2"
        tool2["params"] = dict(tool["params"])
        tool2["params"]["speed"] = {"type": "number", "default": 1.0, "min": 0.1,
                                    "max": 5, "step": 0.1, "keyframeable": True}
        http("POST", "/api/agent/tools", tool2)
        deadline = time.time() + 8
        updated = False
        while time.time() < deadline:
            time.sleep(0.5)
            names = page.eval_on_selector_all(".insp-fx-name", "els => els.map(e => e.textContent)")
            ctrl2 = page.evaluate("""() => {
              const box = [...document.querySelectorAll('.insp-fx')]
                .find(b => b.querySelector('.insp-fx-name')?.textContent === '变焦脉冲 v2');
              return box ? box.querySelectorAll('input[type=range]').length : 0;
            }""")
            if "变焦脉冲 v2" in names and ctrl2 == 2:
                updated = True
                break
        chk("工具改名 + 新增参数后前端自动更新", updated)

        print("=== agent 远端改片段参数 -> 前端实时反映 ===")
        iid = fx["id"]
        cid = list(data["data"]["clips"].keys())[0]
        http("PATCH", "/api/agent/clip/%s/tool/%s?project_id=%s" % (cid, iid, pid),
             {"params": {"amount": 0.1}})
        time.sleep(1.2)
        val = page.evaluate("""() => {
          const box = [...document.querySelectorAll('.insp-fx')]
            .find(b => b.querySelector('.insp-fx-name')?.textContent.startsWith('变焦脉冲'));
          if (!box) return null;
          const num = box.querySelector('input[type=number]');
          return num ? parseFloat(num.value) : null;
        }""")
        chk("agent 远端改参 -> 前端数值同步", val is not None and abs(val - 0.1) < 1e-6, val)

        page.screenshot(path="data/_shot_agent_live.png")
        print("截图 -> data/_shot_agent_live.png")

        http("DELETE", "/api/agent/tools/" + TOOL_ID)
        browser.close()

    print()
    if errors:
        print("失败 %d 项：" % len(errors))
        for e in errors[:20]:
            print("  ", e[:300])
        sys.exit(1)
    print("AGENT LIVE LOOP ALL PASS")


if __name__ == "__main__":
    main()


# __EOF__