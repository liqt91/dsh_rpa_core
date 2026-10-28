"""覆盖层能力实证 v2（M65-S4）：透明 / 抠色 / 穿透 / 跟随，逐条给读数。

v1 的结论（保留在此，因为它是 v2 的起点）：
- `transparent=True` + `frameless=True` + `on_top=True` 能建窗口，红框能画；
- 但截图像素取样显示整片背景是 **(240,240,240)** = WinForms 默认 `Control` 色
  ⇒ pywebview 只把 **WebView2 页面**背景设透明，Form 本身仍不透明，视觉上是一块灰板；
- `WS_EX_TRANSPARENT` 能设上，穿透时页面收不到 mousemove（0 次），恢复后收到（3 次）。

v2 补两件事：
1. **抠色透明**（`BackColor=Magenta` + `LWA_COLORKEY`）能不能把灰板抠透 ——
   判据不是"我看着像"，而是 `F0F0F0` 与洋红的像素占比。
2. 穿透判据改用**根窗口 + 进程 id**，确认鼠标命中的是"别的进程的窗口"而不是自家子窗口。

跑法：`./.venv/Scripts/python.exe overlay_probe.py`（在 ppx_probe 目录下）
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes

import webview

TITLE = "PPXOverlayProbe"
WIN_W, WIN_H = 760, 440
SHOT_DIR = r"D:\Users\Administrator\Documents\代码\rpa_core\.harness\demo\ppx_probe"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY = 0x00000001
GA_ROOT = 2
GREY = (240, 240, 240)
MAGENTA = (255, 0, 255)
COLORREF_MAGENTA = 0x00FF00FF  # 0x00BBGGRR

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin:0; height:100%; background:transparent; overflow:hidden;
               font:13px/1.4 "Microsoft YaHei", sans-serif; }
  #box { position:absolute; left:0; top:0; width:200px; height:100px;
         border:2px solid #ff3b30; border-radius:4px; box-sizing:border-box;
         box-shadow:0 0 0 1px rgba(0,0,0,.65), inset 0 0 0 1px rgba(255,255,255,.35); }
  #hud { position:absolute; left:10px; bottom:10px; padding:3px 8px; border-radius:4px;
         background:rgba(0,0,0,.62); color:#f2f2f2; font-size:12px; }
</style></head><body>
<div id="box"></div>
<div id="hud">booting</div>
<script>
  window.__mm = 0;
  window.addEventListener('mousemove', () => { window.__mm += 1; });
  window.moveBox = (x, y, tag) => {
    document.getElementById('box').style.transform = `translate(${x}px, ${y}px)`;
    document.getElementById('hud').textContent = `${tag}  ${x},${y}  mousemove=${window.__mm}`;
    return window.__mm;
  };
  window.moveBox(0, 0, 'ready');
</script></body></html>"""

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.restype = ctypes.c_long


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return "?"


def find_window(title: str) -> int:
    return int(user32.FindWindowW(None, title) or 0)


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def root_of(hwnd: int) -> int:
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_from_point(x: int, y: int) -> int:
    return int(user32.WindowFromPoint(POINT(int(x), int(y))))


def hit(x: int, y: int, own_hwnd: int) -> dict[str, object]:
    """鼠标命中点归属。

    ★ 不能用「pid 是不是自己」判归属：WebView2 的渲染跑在**独立进程**
    （`msedgewebview2.exe`）里，`Chrome_RenderWidgetHostHWND` 的 pid 永远不等于本进程。
    v2 就是这么误判成 `clickthrough_effective: false` 的。正确判据是
    `GetAncestor(hwnd, GA_ROOT) == 我们的顶层窗口 hwnd`。
    """
    hwnd = window_from_point(x, y)
    root = root_of(hwnd)
    return {
        "hwnd": hwnd,
        "class": class_of(hwnd),
        "root": root,
        "pid": pid_of(hwnd),
        "is_own_window": root == own_hwnd,
    }


def set_clickthrough(hwnd: int, on: bool) -> tuple[int, int]:
    before = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    styles = before | WS_EX_LAYERED | WS_EX_TRANSPARENT if on else before & ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
    return before, styles


def grab(name: str, box: tuple[int, int, int, int]) -> dict[str, object]:
    from PIL import Image, ImageGrab

    path = f"{SHOT_DIR}\\_overlay_{name}.png"
    image = ImageGrab.grab(bbox=box).convert("RGB")
    image.save(path)
    pixels = list(image.getdata())
    total = len(pixels)
    return {
        "path": path,
        "size": list(image.size),
        "grey_f0f0f0_ratio": round(sum(1 for p in pixels if p == GREY) / total, 4),
        "magenta_ratio": round(sum(1 for p in pixels if p == MAGENTA) / total, 4),
    }


def apply_colorkey(window: webview.Window, hwnd: int) -> dict[str, object]:
    """把 Form 背景改成洋红再抠掉——WinForms 做"透明"的老办法。

    两条路都试，因为它们是同一机制的两个入口：
    - `form.TransparencyKey`：WinForms 自己的属性，内部也是 `SetLayeredWindowAttributes`；
    - 直接调 API：v2 试过这条路**失败了**（返回 0），所以这里把 `GetLastError` 一起报出来，
      否则只知道"没成"、不知道为什么。

    `BackColor` 是 .NET 属性，worker 线程不是 UI 线程，直接赋值会被跨线程检查拦下 ⇒
    用 `Control.Invoke` 回 UI 线程。
    """
    out: dict[str, object] = {}
    try:
        from System import Action
        from System.Drawing import Color

        form = window.native
        form.Invoke(Action(lambda: setattr(form, "BackColor", Color.Magenta)))
        time.sleep(0.15)
        form.Invoke(Action(lambda: setattr(form, "TransparencyKey", Color.Magenta)))
        time.sleep(0.15)
        out["transparency_key"] = "set"
    except Exception as exc:  # noqa: BLE001 - 失败也是结论
        out["transparency_key"] = f"{type(exc).__name__}: {exc}"

    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_ubyte,
        wintypes.DWORD,
    ]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    ok = bool(user32.SetLayeredWindowAttributes(hwnd, COLORREF_MAGENTA, 0, LWA_COLORKEY))
    out["api"] = {"ok": ok, "last_error": ctypes.get_last_error()}
    return out


def find_red(path: str) -> list[int] | None:
    """在截图里找第一个红色像素（红框框线），返回**窗口内**坐标。

    不用页面坐标换算：DPI 缩放下 760×440 的请求实测变成 744×401，页面坐标 × 缩放
    这种换算是坑。直接从截图找，坐标天然就是物理像素。
    """
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, height = image.size
    pixels = image.load()
    for y in range(0, height, 2):
        for x in range(width):
            r, g, b = pixels[x, y]
            if r > 180 and g < 90 and b < 90:
                return [x, y]
    return None


def worker(window: webview.Window) -> None:
    report: dict[str, object] = {"pywebview": pkg_version("pywebview"), "self_pid": os.getpid()}
    origin = POINT()
    user32.GetCursorPos(ctypes.byref(origin))
    try:
        time.sleep(1.8)  # 等 transparent 的 show/hide hack 走完
        hwnd = find_window(TITLE)
        report["hwnd"] = hwnd
        report["visible"] = bool(user32.IsWindowVisible(hwnd))
        if not hwnd:
            report["fatal"] = "FindWindowW 找不到窗口"
            return
        left, top, right, bottom = rect_of(hwnd)
        report["rect"] = [left, top, right - left, bottom - top]
        cx, cy = (left + right) // 2, (top + bottom) // 2

        # —— 阶段 1：pywebview 的 transparent=True 到底透不透 ——
        report["shot_1_transparent"] = grab("1_transparent", (left, top, right, bottom))

        # —— 阶段 2：抠色透明 ——
        report["colorkey"] = apply_colorkey(window, hwnd)
        time.sleep(0.4)
        report["shot_2_colorkey"] = grab("2_colorkey", (left, top, right, bottom))

        # —— 阶段 2b：抠色后"哪块吃鼠标"（透明区 vs 红框框线）——
        # colorkey 的语义是"该颜色的像素对鼠标也透明"，所以红框本身会吃鼠标——
        # 拾取时框跟着光标走，光标划过框线就会点在框上。这条必须实测，不能靠推断。
        red = find_red(str(report["shot_2_colorkey"]["path"]))
        report["red_pixel"] = red
        probes: dict[str, tuple[int, int]] = {"transparent_center": (cx, cy)}
        if red is not None:
            probes["red_border"] = (left + red[0], top + red[1])
        report["hits_no_ws_ex"] = {k: hit(x, y, hwnd) for k, (x, y) in probes.items()}
        set_clickthrough(hwnd, True)
        time.sleep(0.3)
        report["hits_with_ws_ex"] = {k: hit(x, y, hwnd) for k, (x, y) in probes.items()}
        set_clickthrough(hwnd, False)
        time.sleep(0.2)

        # —— 阶段 3：穿透判据（根窗口 + 进程）——
        before = hit(cx, cy, hwnd)
        ex_before, ex_after = set_clickthrough(hwnd, True)
        time.sleep(0.35)
        after = hit(cx, cy, hwnd)
        report["hit_before"] = before
        report["hit_after"] = after
        report["exstyle"] = {"before": hex(ex_before), "after": hex(ex_after)}
        report["clickthrough_effective"] = bool(
            before["is_own_window"] and not after["is_own_window"]
        )

        # —— 阶段 4：穿透时还收不收 mousemove（对照：恢复后应收到）——
        mm0 = window.evaluate_js("window.__mm")
        user32.SetCursorPos(cx - 40, cy - 30)
        time.sleep(0.25)
        user32.SetCursorPos(cx + 40, cy + 30)
        time.sleep(0.45)
        mm1 = window.evaluate_js("window.__mm")
        report["mousemove_clickthrough"] = [mm0, mm1]

        set_clickthrough(hwnd, False)
        time.sleep(0.25)
        mm2 = window.evaluate_js("window.__mm")
        user32.SetCursorPos(cx - 30, cy - 20)
        time.sleep(0.22)
        user32.SetCursorPos(cx + 30, cy + 20)
        time.sleep(0.45)
        mm3 = window.evaluate_js("window.__mm")
        report["mousemove_restored"] = [mm2, mm3]

        # —— 阶段 5：轮询 GetCursorPos 推坐标，红框跟不跟 ——
        set_clickthrough(hwnd, True)
        pt = POINT()
        shots = []
        for i, (dx, dy) in enumerate(((120, 90), (420, 260))):
            user32.SetCursorPos(left + dx, top + dy)
            user32.GetCursorPos(ctypes.byref(pt))
            window.evaluate_js(f"window.moveBox({dx - 8}, {dy - 8}, 'poll{i}')")
            time.sleep(0.4)
            shots.append(grab(f"3_poll{i}", (left, top, right, bottom)))
        report["poll_shots"] = shots
        report["poll_targets"] = [[120, 90], [420, 260]]
    except Exception as exc:  # noqa: BLE001 - 探针把异常本身当证据
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        user32.SetCursorPos(origin.x, origin.y)
        print("PROBE_REPORT " + json.dumps(report, ensure_ascii=False))
        time.sleep(0.2)
        window.destroy()


def main() -> int:
    x, y = 120, 120
    try:
        screens = getattr(webview, "screens", None)
        screen = screens[0] if screens else None
    except Exception:  # noqa: BLE001
        screen = None
    if screen is not None:
        x = max(40, (screen.width - WIN_W) // 2)
        y = max(40, (screen.height - WIN_H) // 3)
    window = webview.create_window(
        TITLE,
        html=HTML,
        width=WIN_W,
        height=WIN_H,
        x=x,
        y=y,
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
        focus=False,
        shadow=False,
    )
    webview.start(worker, window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
