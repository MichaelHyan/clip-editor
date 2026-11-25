# -*- coding: utf-8 -*-
"""浏览器实测：字幕 Delete 删除 + 音频 IndexedDB 缓存。

注意：state 没有挂到 window，脚本里一律用动态 import 拿。

  A. 字幕
     1. 点顶栏「字幕」后 project.subtitles +1
     2. 时间轴 .tl-sub 出现且带 .selected
     3. 按 Delete 后字幕归 0、节点消失、selectedSubtitleId 清空
     4. Backspace 同样能删

  B. 音频缓存
     1. audio_cache.js 可加载、IndexedDB 可用
     2. put -> get -> remove 全链路
     3. 缓存键格式（指纹优先 / path:size:kind 兜底）
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

errors = []
results = []

JS_STATE = """
async () => {
  const m = await import('/static/js/state.js');
  return {
    subs: (m.state.project && m.state.project.subtitles || []).length,
    sid: m.state.selectedSubtitleId || null,
    cid: m.state.selectedClipId || null,
  };
}
"""


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  [OK] " if ok else "  [FAIL] ") + name + (("  -> " + str(detail)) if detail else ""))


def st(page):
    return page.evaluate(JS_STATE)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 950})
        page = ctx.new_page()

        page.on("console", lambda m: errors.append(("console:" + m.text))
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))

        page.goto(BASE, wait_until="networkidle", timeout=30000)
        time.sleep(1.5)
        page.wait_for_selector("#timeline .tl-lane", timeout=10000)

        # ---------------------------------------------------------- A. 字幕删除

        print("\n== A. 字幕 Delete 删除 ==")

        # 清空已有字幕，保证基线干净
        page.evaluate("""async () => {
          const m = await import('/static/js/state.js');
          const subs = (m.state.project && m.state.project.subtitles) || [];
          for (const s of subs) {
            await fetch('/api/projects/' + m.state.pid + '/subtitles/' + s.id,
                        { method: 'DELETE' });
          }
          await m.refresh();
        }""")
        time.sleep(0.6)

        s0 = st(page)
        rec("初始字幕数为 0", s0["subs"] == 0, s0["subs"])

        page.click("#btn-subtitle")
        time.sleep(1.3)

        s1 = st(page)
        rec("点「字幕」后 +1", s1["subs"] == 1, s1["subs"])
        rec("新字幕被选中", bool(s1["sid"]), s1["sid"])

        node_count = page.locator(".tl-sub").count()
        rec("时间轴出现 .tl-sub", node_count >= 1, node_count)

        sel_class = page.evaluate("() => !!document.querySelector('.tl-sub.selected')")
        rec(".tl-sub 带 selected 类", sel_class)

        insp = page.evaluate("""() => {
          const t = document.querySelector('.insp-title');
          return t ? t.textContent.trim() : '';
        }""")
        rec("检查器切到字幕面板", insp == "字幕", insp)

        page.keyboard.press("Delete")
        time.sleep(1.3)

        s2 = st(page)
        rec("Delete 后字幕归 0", s2["subs"] == 0, s2["subs"])
        rec("selectedSubtitleId 已清空", not s2["sid"], s2["sid"])

        node_after = page.locator(".tl-sub").count()
        rec(".tl-sub 节点消失", node_after == 0, node_after)

        # Backspace
        page.click("#btn-subtitle")
        time.sleep(1.3)
        mid = st(page)["subs"]
        page.keyboard.press("Backspace")
        time.sleep(1.3)
        end = st(page)["subs"]
        rec("Backspace 同样能删", mid == 1 and end == 0, "mid=%s end=%s" % (mid, end))

        # ---------------------------------------------------------- B. 音频缓存

        print("\n== B. 音频 IndexedDB 缓存 ==")

        cache = page.evaluate("""async () => {
          try {
            const m = await import('/static/js/audio_cache.js');
            if (typeof m.audioCacheKey !== 'function') return {ok:false, why:'audioCacheKey 缺失'};
            if (typeof m.MAX_ENTRY_BYTES !== 'number') return {ok:false, why:'MAX_ENTRY_BYTES 缺失'};

            const key = 'unittest:probe';
            const payload = new Uint8Array([1,2,3,4,5,6,7,8]).buffer;
            const putOk = await m.put(key, payload);
            const got = await m.get(key);
            const hitLen = got ? got.byteLength : 0;
            const stt = await m.stats();
            await m.remove(key);
            const gone = await m.get(key);

            return {
              ok: true,
              putOk,
              hitLen,
              dbAvailable: stt.available,
              goneAfterRemove: gone === null,
              keyFp: m.audioCacheKey({probe:{fingerprint:'abc123'}, kind:'audio'}, 'D:/x.mp4'),
              keyNoFp: m.audioCacheKey({probe:{size:999}, kind:'video'}, 'D:/y.mp4'),
            };
          } catch (e) {
            return {ok:false, why:String(e)};
          }
        }""")

        rec("audio_cache.js 可加载", cache.get("ok"), cache.get("why", ""))
        if cache.get("ok"):
            rec("put 成功", cache.get("putOk"))
            rec("get 命中且长度=8", cache.get("hitLen") == 8, cache.get("hitLen"))
            rec("IndexedDB 可用", cache.get("dbAvailable"))
            rec("remove 后读不到", cache.get("goneAfterRemove"))
            rec("指纹键 = abc123:audio", cache.get("keyFp") == "abc123:audio", cache.get("keyFp"))
            rec("无指纹兜底 = path:size:kind",
                cache.get("keyNoFp") == "D:/y.mp4:999:video", cache.get("keyNoFp"))

        # ---------------------------------------------------------- 汇总

        print("\n== 控制台错误 ==")
        real = [e for e in errors if "favicon" not in str(e[1]).lower()]
        if real:
            for t, m in real[:10]:
                print("  ", t, ":", m[:220])
        else:
            print("  无")

        fails = [r for r in results if not r[1]]
        print("\n== 汇总：%d/%d 通过 ==" % (len(results) - len(fails), len(results)))
        browser.close()
        return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

# __EOF__