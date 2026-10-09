"""M47.12 活体验证：新截图链路 / 默认页签 / 树行文本 / 加宽 / 描述精简。

**只跑离线判据，不起真机窗口**（记忆铁律：起真机靶子前先问维护者是否占机）。
想验证「真的截到了图」的那部分留到真机复验，本脚本先证明**接线与口径**：

1. ``_browser_preview`` / ``_desktop_preview_callable`` 的载荷形状（用假verify 与
   假 hwnd，绝不真截屏）；
2. 两条腿**红框换算真的接上了**（桌面 1:1 / 浏览器 scale×偏移）；
3. 默认页签 = 精准定位（browser + desktop 两条腿）；
4. ``node_label`` 树行文本口径；
5. 加宽下限；
6. 描述区精简后的确切文本。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rpa_core.capture import screen_shot  # noqa: E402
from rpa_core.gui.app import (  # noqa: E402
    _browser_preview,
    _desktop_preview_callable,
)

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} | {label} | got={got!r}")
    if not ok:
        print(f"       want={want!r}")
        FAILURES.append(label)


# ---- 1+2. 假 API 走通两条腿的完整取图与红框 ------------------------------------
# 固定尺寸的真 PNG：浏览器腿的 ``scale = 图宽 / viewport.width`` 因此**精确可算**
# （2560/1280 = 2.0），不必用「符号为正」这种弱判据糊过去。
# 探针生成一次落到``.harness/spike/_probe_2560.png``，避免每次跑都压一遍 zlib。
_PNG_PATH = ROOT / ".harness" / "spike" / "_probe_2560.png"
if not _PNG_PATH.exists():
    import struct
    import zlib

    def _make_png(width: int, height: int) -> bytes:
        raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))

        def chunk(tag: bytes, data: bytes) -> bytes:
            body = struct.pack(">I", len(data)) + tag + data
            return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    _PNG_PATH.write_bytes(_make_png(2560, 1440))
_PNG_2560 = _PNG_PATH.read_bytes()


class _FakeApi:
    """记录调用的假屏幕 API —— 绝不碰真桌面。"""

    def __init__(self, grab_ok: bool = True) -> None:
        self.grab_ok = grab_ok
        self.grabs: list[tuple] = []

    def window_rect(self, hwnd):
        return (-8, -1088, 1928, -32)  # 真机 msedge 的负坐标多屏矩形

    def grab(self, bbox):
        self.grabs.append(bbox)
        return _PNG_2560 if self.grab_ok else None

    def window_exists(self, hwnd):
        return True


app = QApplication.instance() or QApplication([])

api = _FakeApi()
desktop_shot = screen_shot.shot_for_desktop(4242, {"left": 100, "top": 200,
                                                    "width": 80, "height": 30}, api=api)
check("桌面腿：截到的是窗口那块屏幕（负坐标多屏照样成立）",
      api.grabs[0], (-8, -1088, 1928, -32))
check("桌面腿：红框 = 屏幕坐标 − 窗口原点（同源 1:1，无 scale/滚动/dpr）",
      desktop_shot["box"], {"x": 108.0, "y": 1288.0, "width": 80.0, "height": 30.0})

# 浏览器腿要**另一个替身形状**：桌面腿的元素矩形是**屏幕绝对坐标**（所以元素 y=200
# 落在窗口内），而浏览器腿的 rect 是**视口内 CSS 像素**，换算前必须加视口原点偏移。
# 沿用同一个负坐标窗口去构造rect 会得到 y ≈ 2384 > 图高 1440 ⇒ 正确地判成「在视口
# 外」⇒ 没有 box。这是判据设计的坑（不是实现的 bug）：**两条腿的 rect 坐标系不同**。
api2 = _FakeApi()
api2_rect = (-8, -100, 1928, 956)
api2.window_rect = lambda hwnd: api2_rect
browser_shot = screen_shot.shot_for_browser(
    5150,
    {"left": 10, "top": 20, "width": 300, "height": 40},
    {"width": 1280, "height": 720, "screenX": 12, "screenY": 84},
    api=api2,
)
# scale = 2560/1280 = 2.0（用图**实际宽**，不用 devicePixelRatio 猜）
# 视口原点 (12,84) 相对窗口 (-8,-100) 的偏移 = (+20, +184)
# ⇒ x = 10*2 + 20*2 = 60；y = 20*2 + 184*2 = 408
check("浏览器腿：红框 = rect×scale + 视口原点偏移×scale",
      browser_shot["box"],
      {"x": 60.0, "y": 408.0, "width": 600.0, "height": 80.0})
check("浏览器腿：截的是同一个窗口矩形（不是视口）",
      api2.grabs[0], (-8, -100, 1928, 956))

# ---- 3. 载荷形状（GUI 侧真正消费的就是这三个键）--------------------------------
class _FakeVerifier:
    """`_browser_preview` 要的是带 ``.verify`` 的对象（不是裸函数）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def verify(self, css, want_shot=False):
        self.calls.append((css, want_shot))
        return {
            "count": 1,
            "windowHandle": 4242,
            "rect": {"left": 100, "top": 200, "width": 80, "height": 30},
            "viewport": {"width": 1280, "height": 720,
                         "screenX": 12, "screenY": 84},
        }


def _fake_preview(result):
    return {"dataUrl": "data:image/png;base64,AAAA", "box": {"x": 1, "y": 2,
                                                           "width": 3, "height": 4},
            "count": result.get("count"), "windowOrigin": [0, 0],
            "imageSize": [100, 100]}


import rpa_core.capture.screen_shot as screen_mod  # noqa: E402

screen_mod.browser_preview_shot = _fake_preview

verifier = _FakeVerifier()
out = _browser_preview(verifier, "#sb")
check("browser 预览：verify 以 want_shot=True 被调用", verifier.calls[0], ("#sb", True))
check("browser 预览：载荷三键齐（dataUrl / box / count）",
      sorted(k for k in out if k in ("dataUrl", "box", "count")), ["box", "count", "dataUrl"])

# ---- 3b. 桌面预览闭包**无参**且已封好句柄与矩形 --------------------------------
_desktop_doc = {
    "kind": "desktop",
    "metadata": {"windowHandle": 4242,
                 "rect": {"left": 100, "top": 200, "width": 80, "height": 30}},
}
_closed_calls: list[tuple] = []
_real_desktop = screen_mod.desktop_preview_shot


def _fake_desktop(hwnd, rect):
    _closed_calls.append((hwnd, tuple(sorted((rect or {}).items()))))
    return {"dataUrl": "data:image/png;base64,AAAA", "windowOrigin": [-8, -100],
            "imageSize": [1936, 1056],
            "box": {"x": 108.0, "y": 300.0, "width": 80.0, "height": 30.0}}


screen_mod.desktop_preview_shot = _fake_desktop
try:
    _shot = _desktop_preview_callable(_desktop_doc)
    check("桌面预览：闭包**无参**（元素就是捕获时那一个，没有 css 可传）",
          _shot.__code__.co_argcount, 0)
    _payload = _shot()
    check("桌面预览：载荷带 dataUrl + box", sorted(
        k for k in _payload if k in ("dataUrl", "box")), ["box", "dataUrl"])
    check("桌面预览：句柄与矩形已封进闭包（调用时不再传参）",
          _closed_calls[0][0], 4242)
finally:
    screen_mod.desktop_preview_shot = _real_desktop

# 缺句柄 / 截屏失败两种降级都要有明确 shotError（GUI 据此出文案）
screen_mod.desktop_preview_shot = lambda hwnd, rect: None
try:
    check("桌面预览：截屏失败 → shotError=shot-failed",
          _desktop_preview_callable(_desktop_doc)().get("shotError"), "shot-failed")
finally:
    screen_mod.desktop_preview_shot = _real_desktop

check("桌面预览：缺句柄时也能调（失败落在闭包里，不炸 GUI）",
      callable(_desktop_preview_callable({"kind": "desktop", "metadata": {}})), True)

# ---- 4. 默认页签 = 精准定位（两条腿）-------------------------------------------
from rpa_core.gui.element_editor import ElementEditorForm  # noqa: E402

browser_form = ElementEditorForm(
    {"kind": "browser", "selector": {"css": "#a"}}
)
check("browser：页签名", [browser_form.tabs.tabText(i)
                     for i in range(browser_form.tabs.count())], ["预览", "精准定位"])
check("browser：默认停在精准定位", browser_form.tabs.currentIndex(), 1)

desktop_form = ElementEditorForm(
    {"kind": "desktop", "selector": {"locator": {"backend": "uia",
                                                 "controlType": "Button"}}}
)
# 桌面腿的页签属性叫 ``desktop_tabs``（browser 腿叫 ``tabs``）——两条腿各建一套
# 控件，不是同一个 QTabWidget 复用。
_dtabs = desktop_form.desktop_tabs
check("desktop：页签名（M47.12 推翻了 M47.11「桌面不摆页签」）",
      [_dtabs.tabText(i) for i in range(_dtabs.count())], ["预览", "精准定位"])
check("desktop：默认停在精准定位", _dtabs.currentIndex(), 1)

# ---- 5. 桌面截图通道无参 + 描述区精简 ------------------------------------------
calls: list[tuple] = []


def _shot_noarg():
    calls.append(())
    return {"shotError": "shot-failed"}


desktop_form.enable_shot_channel(_shot_noarg)
desktop_form._run_shot()

import time as _time  # noqa: E402

deadline = _time.time() + 10
while _time.time() < deadline and not calls:
    app.processEvents()
    _time.sleep(0.02)
check("桌面截图通道无参调用", calls, [()])

from rpa_core.gui.element_editor import attribute_rows, node_label  # noqa: E402

# ``node_label`` 的勾选态不是独立字段，而是由 :func:`attribute_rows` 从
# ``(entry 的id/classes, 当前 fragment)`` **反推**出来——所以喂 entry 时必须
# 同时给 id/classes 与 fragment，只给 fragment 会让所有行都「未勾选」，
# 于是行文本退化成光秃秃的 tag（第一版探针就是这么写的，当场返3 条红）。
_full = {"tag": "div", "id": "a", "classes": ["b", "c"]}
# **期望值必须按捕获口径写，不能按影刀的观感写**：content.js 的口径是
#「带 id 即终止上溯」—— id 等值命中时整层就是 `#a`，tag/class/nth 不再参与。
# 所以 `node_label` 给 `div#a` 是**对的**，真selector 也确实是 `#a`；
# 若按观感写成 `div#a.b:nth-of-type(2)`，判据就会逼着实现偏离捕获口径。
check("node_label：id 等值命中即整层（tag 保留，class/nth 不参与——同捕获口径）",
      node_label({**_full, "fragment": "div#a"}), "div#a")

_noid = {"tag": "div", "id": "", "classes": ["b", "c"]}
# nth 行要entry 带 ``nthOfType``（int >= 1）才会生成——它没有「从 fragment 反推
# 出值」的渠道（值本身就在 fragment 里，但值是谁给的得来自捕获侧）。
_nth = {"tag": "div", "id": "", "classes": ["b", "c"], "nthOfType": 2}
check("node_label：class 逐个显示（未勾的 c 不出现）",
      node_label({**_nth, "fragment": "div.b:nth-of-type(2)"}),
      "div.b:nth-of-type(2)")
check("node_label：nth 没勾上就不显示",
      node_label({**_nth, "fragment": "div.b"}), "div.b")
check("node_label：只有 fragment 里出现的 class 才算勾上",
      node_label({**_noid, "fragment": "div.c"}), "div.c")
check("node_label：未勾选任何属性 → 只剩 tag（刻意不显示未勾的）",
      node_label({"tag": "span", "id": "", "classes": ["x"],
                  "fragment": "span"}), "span")
check("node_label：没有 tag 但有勾中的 class → 给 '.cls'（不必退化成 '?'）",
      node_label({"tag": "", "classes": ["b"], "fragment": ".b"}), ".b")
check("node_label：tag 与勾选全空才给 '?'（不留空行）",
      node_label({"tag": "", "id": "", "classes": [], "fragment": ""}), "?")

# **同源**这件事要拿真东西比，不能自己比自己（恒真）。口径：树行文本必须与
# 「用同一批勾选编出来的 fragment」逐字相等（``#a`` ⇒ ``div#a``，即行文本 =
# tag + fragment）。
_rows = attribute_rows(_full, "div#a")
from rpa_core.gui.element_editor import compile_fragment  # noqa: E402

_compiled = compile_fragment(_full, _rows)
check("同源：树行文本 == tag + compile_fragment 的结果",
      node_label({**_full, "fragment": "div#a"}), f"div{_compiled}")

from rpa_core.gui.element_panel import ElementDialog  # noqa: E402

desk_dialog = ElementDialog(
    {
        "kind": "desktop",
        "selector": {"locator": {"backend": "uia", "controlType": "Button"}},
        "verifyCount": 1,
        "metadata": {"windowTitle": "记事本", "controlType": "Button",
                     "automationId": "okBtn", "name": "确定"},
    },
    default_name="x",
)
desk_meta = desk_dialog.meta_label.text()
check("描述区（uia）：只留所在窗口",
      desk_meta, "所在窗口：记事本")

win_dialog = ElementDialog(
    {
        "kind": "desktop",
        "selector": {"locator": {"backend": "win32",
                                 "className": "WindowsForms10.BUTTON.app.0"}},
        "verifyCount": 1,
        "metadata": {"windowTitle": "记事本",
                     "className": "WindowsForms10.BUTTON.app.0"},
    },
    default_name="x",
)
check("描述区（win32）：窗口 + 类名用途提示",
      win_dialog.meta_label.text(),
      "所在窗口：记事本\n提示：该控件无窗口文本，win32 定位靠类名（下方字段表可勾选）")

browser_dialog = ElementDialog(
    {
        "kind": "browser",
        "selector": {"css": "#a"},
        "verifyCount": 1,
        "metadata": {"tag": "textarea", "id": "a", "rect": {"x": 1, "y": 2,
                                                            "width": 3, "height": 4},
                     "role": "searchbox", "url": "https://x.test/"},
    },
    default_name="x",
)
check("描述区（browser）：落到占位说明",
      browser_dialog.meta_label.text(),
      "（捕获信息已全部收进上方可勾选的属性/字段表，此处不再重复展示）")

# ---- 6. 加宽下限 ---------------------------------------------------------------
from rpa_core.gui.element_editor import (  # noqa: E402
    _DIALOG_DEFAULT_SIZE,
    _DIALOG_MIN_WIDTH,
    _open_wide_enough,
)

check("加宽下限", _DIALOG_MIN_WIDTH, 860)
check("默认尺寸", _DIALOG_DEFAULT_SIZE, (860, 760))
check("旧窄持久化值被顶到下限", _open_wide_enough((680, 720)), (860, 720))
check("旧宽值保留（只抬到下限，不压回）", _open_wide_enough((1200, 700)), (1200, 700))
check("无记录用默认值", _open_wide_enough(None), _DIALOG_DEFAULT_SIZE)

print()
if FAILURES:
    print(f"{len(FAILURES)} 项失败：")
    for item in FAILURES:
        print(f"  - {item}")
    raise SystemExit(1)
print("全部通过")