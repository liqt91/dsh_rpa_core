"""M107 S0 探针：验证「运行时截图 → 图像定位 → 物理鼠标点击」产出 isTrusted=true。

跑法（会用**维护者已安装扩展的真实浏览器**新开一个标签页，并真实移动鼠标）：

    .venv/Scripts/python.exe .harness/spike/probe_m107_trusted_click.py

为什么不用 L2 靶场的 `l2_browser_session`：本机 Edge 157 会忽略命令行
`--load-extension`（实验见 `diag_m107_load_ext.py`：四个变体 profile 里扩展计数都是 0），
而维护者已安装的扩展（默认端点）是在线的。靶页仍是本地静态页，点坏了没有副作用，
标签页在结束时关掉。

验证四件事：
1. 运行时 ``captureVisibleTab`` 的视口图能裁出元素模板（M102 的几何口径）；
2. ``desktop.imageLocate`` 能在**窗口截图**上把这枚模板找回来（跨图源匹配）；
3. 物理点击砸下去后，页面读到 ``isTrusted=true``（本探针的核心假设）；
4. 点击真的落在目标上（``#status`` 变成 ``clicked:1``），排除「点偏了但也 trusted」。

★ 首版把「注入被拦」和「坐标偏了」报成同一个 ``FAIL``，只能靠猜，白跑一轮往返。
现在分三段取证，各自独立：

* ``injection``：先把光标挪 40px 再挪回来。挪不动 ⇒ 本进程发不出输入
  （UIPI / 安全软件 / 沙箱 hook），此时**点击从未发生**，直接判 ``BLOCKED`` 退出 3；
* ``hoverChain`` / ``hoverOnTarget``：点击前先把光标移到目标上，读页面的 ``:hover``
  链。它不依赖任何坐标换算——含 ``#btn`` 就说明「点的那一点确实在按钮上」，
  于是剩下的失败只可能是事件没送达；
* ``markedShot``：窗口截图 + 红框（找到的矩形）+ 绿十字（要点的那点），
  给人眼一眼复核的机会。

探针不改产品代码；一切失败原样打印，不做兜底掩盖。
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rpa_core.executors.browser import PlaywrightExecutor  # noqa: E402
from rpa_core.executors.browser_ext import ExtensionExecSession  # noqa: E402
from rpa_core.executors.desktop_image import DesktopImageExecutor  # noqa: E402
from rpa_core.extension_exec import ExtensionExecClient  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402
from tests.commands.l2_harness import (  # noqa: E402
    TESTAPPS_BROWSER,
    _QuietHandler,
    _QuietServer,
)

# 靶页上的按钮：点它只改 #status，没有业务副作用。
TARGET = "#btn"
STATUS = "#status"
PAGE_TITLE = "RPA L2 靶页 basic"

# 让按钮有足够纹理，避免纯文字模板在小图上误匹配（靶页可随意改外观）。
DRESS_UP = """
return (function () {
  var b = document.querySelector('#btn');
  if (!b) return 'no-target';
  b.style.cssText = 'padding:14px 28px;font-size:18px;background:#2b6cb0;color:#fff;'
    + 'border:3px solid #1a4e82;border-radius:6px;';
  return 'dressed';
})()
"""

# 监听器：记录每一次 mousedown / click 的 isTrusted 与坐标。
INSTALL_PROBE = """
return (function () {
  window.__rpaProbe = { events: [] };
  var b = document.querySelector('#btn');
  if (!b) return 'no-target';
  var types = ['pointerover', 'mouseover', 'pointerdown',
               'mousedown', 'mouseup', 'click'];
  types.forEach(function (type) {
    b.addEventListener(type, function (e) {
      window.__rpaProbe.events.push({
        type: type,
        isTrusted: e.isTrusted,
        x: e.clientX,
        y: e.clientY,
        button: e.button
      });
    }, true);
  });
  return 'installed';
})()
"""

READ_PROBE = "return window.__rpaProbe || null"

# 光标真的落在按钮上时，页面自己的 :hover 链会包含 #btn —— 这是「坐标对不对」
# 唯一不依赖任何换算的实证（换算链路出错时它一定不含 #btn）。
HOVER_CHAIN = """
return (function () {
  var nodes = document.querySelectorAll(':hover');
  var out = [];
  for (var i = 0; i < nodes.length; i++) {
    out.push(nodes[i].tagName.toLowerCase() + (nodes[i].id ? '#' + nodes[i].id : ''));
  }
  return out;
})()
"""

# 元素矩形 + 视口/窗口几何：视口图裁模板与后续诊断都要用。
# executeScript 是「函数体」语义（background 用 new Function 包一层），必须显式 return。
READ_GEOMETRY = """
return (function () {
  var el = document.querySelector('__TARGET__');
  var r = el.getBoundingClientRect();
  return {
    rect: { left: r.left, top: r.top, width: r.width, height: r.height },
    viewport: {
      width: window.innerWidth || 0,
      height: window.innerHeight || 0,
      dpr: window.devicePixelRatio || 1,
      scrollX: window.scrollX || 0,
      scrollY: window.scrollY || 0,
      screenX: window.screenX,
      screenY: window.screenY,
      outerWidth: window.outerWidth || 0,
      outerHeight: window.outerHeight || 0
    }
  };
})()
""".replace("__TARGET__", TARGET)


def run(executor, command, inputs, step="probe"):
    import asyncio

    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="m107",
        step_id=step,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def brief(result):
    return {
        "status": result.status,
        "outputs": result.outputs,
        "error": (
            {"code": result.error.code, "message": result.error.message} if result.error else None
        ),
    }


REPORT_PATH = ROOT / ".harness" / "spike" / "_m107_out.json"


def emit(report: dict, code: int, line: str | None = None) -> int:
    """报告**先落盘再打印**：谁来跑都留得下证据，不用把一屏 JSON 复制回去。"""
    report.setdefault("reportPath", str(REPORT_PATH))
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    try:
        REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[M107] 报告落盘失败：{exc}", file=sys.stderr)
    if line:
        print(f"[M107] {line}", file=sys.stderr)
    print(text)
    return code


def find_window_by_title(needle: str) -> int | None:
    """按标题找可见顶层窗口。真实浏览器里靶页标签所在窗口的标题就含靶页 title。"""
    try:
        import win32gui
    except ImportError:
        return None
    hits: list[tuple[int, str]] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if needle in title:
            hits.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    if not hits:
        return None
    return hits[0][0]


def ensure_foreground(hwnd: int) -> dict:
    """最小化先恢复，再置前：物理点击只能砸在屏幕上看得见的地方。"""
    info: dict = {}
    try:
        import win32con
        import win32gui

        info["wasIconic"] = bool(win32gui.IsIconic(hwnd))
        if info["wasIconic"]:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        from rpa_core.capture.foreground_window import bring_to_foreground

        info["broughtToForeground"] = bool(bring_to_foreground(hwnd))
    except Exception as exc:  # noqa: BLE001 - 置前失败不致命，点不中会在 status 上暴露
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _elevated(pid: int | None) -> bool | None:
    """进程是否提权。UIPI 下低完整性进程**发不出**输入到高完整性窗口。"""
    try:
        import win32api
        import win32con
        import win32security
    except ImportError:
        return None
    handle = None
    token = None
    try:
        if pid is None:
            handle = win32api.GetCurrentProcess()
        else:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    except Exception:  # noqa: BLE001 - 取不到就回 None，不猜
        return None
    finally:
        for item in (token, handle if pid is not None else None):
            if item is not None:
                try:
                    win32api.CloseHandle(item)
                except Exception:  # noqa: BLE001
                    pass


def window_facts(hwnd: int, point: tuple[int, int], rect: dict) -> dict:
    """窗口 / 虚拟桌面 / 光标 / 权限的事实清单——只报事实，不下结论。

    ``chromeImplied`` 是「匹配结果隐含的视口偏移」：点击点相对窗口原点，减去元素
    在视口里的中心。它应当落在 ``(侧栏+边框, 标签栏+地址栏)`` 那个量级；如果它接近
    0 或某个荒唐的大数，说明模板匹配命中的根本不是目标（或窗口不是这一个）。
    """
    facts: dict = {}
    try:
        import win32api
        import win32gui
        import win32process
    except ImportError:
        return {"error": "pywin32 不可用"}
    wr = win32gui.GetWindowRect(hwnd)
    facts["windowRect"] = list(wr)
    facts["windowSize"] = [wr[2] - wr[0], wr[3] - wr[1]]
    facts["className"] = win32gui.GetClassName(hwnd)
    facts["clientOrigin"] = list(win32gui.ClientToScreen(hwnd, (0, 0)))
    left, top = win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77)
    width, height = win32api.GetSystemMetrics(78), win32api.GetSystemMetrics(79)
    facts["virtualScreen"] = [left, top, width, height]
    facts["pointInVirtualScreen"] = (
        left <= point[0] < left + width and top <= point[1] < top + height
    )
    facts["pointInWindow"] = [point[0] - wr[0], point[1] - wr[1]]
    facts["cursor"] = list(win32gui.GetCursorPos())
    facts["selfElevated"] = _elevated(None)
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        facts["targetPid"] = pid
        facts["targetElevated"] = _elevated(pid)
    except Exception as exc:  # noqa: BLE001
        facts["pidError"] = f"{type(exc).__name__}: {exc}"
    facts["chromeImplied"] = [
        round(point[0] - wr[0] - (rect.get("left", 0) + rect.get("width", 0) / 2), 1),
        round(point[1] - wr[1] - (rect.get("top", 0) + rect.get("height", 0) / 2), 1),
    ]
    return facts


def _token_facts(pid: int | None = None) -> dict:
    """令牌完整性级别——**这是能不能注入的分水岭**。

    Windows 的完整性机制：Low（``S-1-16-4096``）进程发不出任何合成输入，
    ``SendInput`` 会被吞、``SetCursorPos`` 直接失败且**不设错误码**。
    本仓 agent 会话的 python 正好跑在 Low（沙箱降权），所以探针在这里必然 BLOCKED；
    换到中/高完整性的终端（``whoami /groups | findstr Label`` 看得到 Medium/High）就通。

    ★ 传入 ``pid`` 时可取**别的进程**的口径：父进程 High、子进程 Low 这种
    「跨级跳变」本身就是证据——令牌不会继承着降级，是有人重造了令牌。
    """
    try:
        import win32api
        import win32con
        import win32security
    except ImportError:
        return {"tokenError": "pywin32 不可用"}
    try:
        handle = win32api.GetCurrentProcess() if pid is None else win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
    except Exception as exc:  # noqa: BLE001
        return {"tokenError": f"{type(exc).__name__}: {exc}"}
    facts: dict = {}
    try:
        sid = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)[0]
        )
        facts["integrity"] = sid
        level = int(sid.rsplit("-", 1)[-1])
        facts["integrityLevel"] = level
        facts["integrityName"] = {
            0x0000: "Untrusted",
            0x1000: "Low",
            0x2000: "Medium",
            0x3000: "High",
            0x4000: "System",
        }.get(level, f"0x{level:X}")
        facts["restrictedSids"] = len(
            win32security.GetTokenInformation(token, win32security.TokenRestrictedSids)
        )
        facts["canInject"] = level >= 0x2000 and facts["restrictedSids"] == 0
    except Exception as exc:  # noqa: BLE001
        facts["tokenError"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            win32api.CloseHandle(token)
        except Exception:  # noqa: BLE001
            pass
        if pid is not None:
            try:
                win32api.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass
    return facts


def _process_chain(limit: int = 6) -> list[dict]:
    """祖先进程链（pid / 名字）：用来分辨这次到底是在谁的进程树里跑的。

    「注入被拦」时这是唯一能分清**沙箱拦的**还是**机器整体拦的**的证据——
    父进程是 WorkBuddy 的终端，和一个从「开始菜单 → cmd」起来的进程，结论完全不同。
    """
    try:
        import ctypes
        from ctypes import wintypes as wt
    except ImportError:
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (0, -1):
        return []
    parents: dict[int, tuple[int, str]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
        while True:
            parents[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snapshot)

    chain: list[dict] = []
    pid = int(kernel32.GetCurrentProcessId())
    for _ in range(limit):
        parent, name = parents.get(pid, (0, "?"))
        chain.append({"pid": pid, "name": name, "parentPid": parent})
        if parent == 0 or parent == pid:
            break
        pid = parent
    return chain


def environment_facts() -> dict:
    """本进程在哪个桌面 / 会话 / 进程树里——注入被拦时靠它分清是谁拦的。"""
    try:
        import ctypes
        from ctypes import wintypes as wt
    except ImportError:
        return {"error": "ctypes 不可用"}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def name_of(handle: object) -> str | None:
        if not handle:
            return None
        buf = ctypes.create_unicode_buffer(256)
        needed = wt.DWORD()
        ok = user32.GetUserObjectInformationW(
            handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)
        )
        return str(buf.value) if ok else None

    current = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    input_desk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    session = wt.DWORD()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session))
    facts = {
        "desktop": name_of(current),
        "inputDesktop": name_of(input_desk),
        "windowStation": name_of(user32.GetProcessWindowStation()),
        "sessionId": int(session.value),
        "selfElevated": _elevated(None),
        "compatLayer": os.environ.get("__COMPAT_LAYER"),
        "chain": _process_chain(),
    }
    facts.update(_token_facts())
    # 父进程的完整性：令牌不会「继承着降级」，High 父 + Low 子 ⇒ 有人重造了令牌
    parent = (facts["chain"][0].get("parentPid") if facts["chain"] else None) or None
    if parent:
        parent_facts = _token_facts(int(parent))
        facts["parentIntegrity"] = parent_facts.get("integrity")
        facts["parentIntegrityName"] = parent_facts.get("integrityName")
        facts["inheritedIntegrity"] = facts.get("integrity") == parent_facts.get("integrity")
    # 不在「输入桌面」上 ⇒ 注入永远到不了用户眼前的屏幕（GetCursorPos 还会给假读数）
    facts["onInputDesktop"] = bool(
        facts["desktop"] and facts["desktop"] == facts["inputDesktop"]
    )
    if input_desk:
        user32.CloseDesktop(input_desk)
    return facts


def _set_cursor(point: tuple[int, int]) -> dict:
    """静态 API 对照：SetCursorPos 与 SendInput 谁失效，故障面完全不同。"""
    try:
        import ctypes
    except ImportError:
        return {"ok": None}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ok = user32.SetCursorPos(int(point[0]), int(point[1]))
    return {"ok": bool(ok), "error": int(ctypes.get_last_error())}


def preflight_injection() -> dict:
    """先证明「本进程真能把光标挪走」：挪不动就说明注入被拦，后面的点击等于没点。

    M107 S0 首版的教训：注入被拦与坐标偏了报的是同一个 ``FAIL``，白跑一轮往返。
    这里同时打两条路：``SendInput``（产品实际走的）与 ``SetCursorPos``（静态对照）。
    两条都失败才是「进程发不出输入」；只有 SendInput 失败则是参数/结构问题。
    """
    from rpa_core.executors import human_input as hi

    api = hi.default_api()
    start = api.cursor_pos()
    target = (start[0] + 40, start[1] + 40)

    hi.move_to(target[0], target[1], duration_ms=0)
    time.sleep(0.15)
    after_send = api.cursor_pos()
    set_result = _set_cursor(target)
    time.sleep(0.1)
    after_set = api.cursor_pos()
    hi.move_to(start[0], start[1], duration_ms=0)
    _set_cursor(start)
    time.sleep(0.1)

    def _hit(pos: tuple[int, int]) -> bool:
        return abs(pos[0] - target[0]) <= 3 and abs(pos[1] - target[1]) <= 3

    return {
        "before": list(start),
        "expected": list(target),
        "afterSendInput": list(after_send),
        "sendInputLanded": _hit(after_send),
        "setCursorPos": set_result,
        "afterSetCursorPos": list(after_set),
        "setCursorPosLanded": _hit(after_set),
        "restored": list(api.cursor_pos()),
        "moved": _hit(after_send) or (_hit(after_set) and set_result.get("ok") is True),
    }


def save_marked_window(hwnd: int, located: dict, point: tuple[int, int]) -> dict | None:
    """存一张「窗口截图 + 找到的矩形（红框）+ 要点的那一点（绿十字）」，供人眼复核。"""
    try:
        import base64 as _b64
        import io as _io

        from PIL import Image, ImageDraw

        from rpa_core.capture.screen_shot import grab_window
    except ImportError:
        return None
    shot = grab_window(hwnd)
    if not shot:
        return None
    ox, oy = shot["windowOrigin"]
    raw = _b64.b64decode(str(shot["dataUrl"]).split(",", 1)[-1])
    img = Image.open(_io.BytesIO(raw)).convert("RGB")
    draw = ImageDraw.Draw(img)
    box = [
        located["left"] - ox,
        located["top"] - oy,
        located["left"] - ox + located["width"],
        located["top"] - oy + located["height"],
    ]
    draw.rectangle(box, outline=(255, 0, 0), width=2)
    cx, cy = point[0] - ox, point[1] - oy
    draw.line([(cx - 14, cy), (cx + 14, cy)], fill=(0, 220, 0), width=2)
    draw.line([(cx, cy - 14), (cx, cy + 14)], fill=(0, 220, 0), width=2)
    out = ROOT / ".harness" / "spike" / "_m107_window_marked.png"
    img.save(out)
    return {"path": str(out), "imageSize": list(img.size), "origin": [ox, oy]}


def cursor_now() -> list[int] | None:
    try:
        import win32gui

        return list(win32gui.GetCursorPos())
    except Exception:  # noqa: BLE001
        return None


def foreground_now(hwnd: int) -> dict:
    """按下前那一刻的前台窗口——「点了但只是在激活窗口」会把点击吞掉。"""
    try:
        import win32gui

        fg = int(win32gui.GetForegroundWindow())
        return {
            "hwnd": fg,
            "title": win32gui.GetWindowText(fg),
            "isTarget": fg == int(hwnd),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def crop_template(png_path: Path, rect: dict, viewport: dict) -> tuple[bytes, dict] | None:
    """从视口图里裁出元素模板。几何只走 `viewport_box_in_image`（M102 权威口径）。"""
    try:
        from PIL import Image

        from rpa_core.capture.screen_shot import viewport_box_in_image
    except ImportError:
        return None
    img = Image.open(png_path)
    box = viewport_box_in_image(rect, viewport, img.size)
    if not box:
        return None
    # 外扩 4px：模板贴着元素边缘时，抗锯齿/阴影会让纯边界匹配偏脆。
    margin = 4
    left = max(0, int(box["x"]) - margin)
    top = max(0, int(box["y"]) - margin)
    right = min(img.size[0], int(box["x"] + box["width"]) + margin)
    bottom = min(img.size[1], int(box["y"] + box["height"]) + margin)
    if right <= left or bottom <= top:
        return None
    cropped = img.crop((left, top, right, bottom)).convert("RGB")
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue(), {
        "boxInImage": box,
        "cropSize": [cropped.size[0], cropped.size[1]],
        "imageSize": list(img.size),
    }


def main() -> int:
    report: dict = {"steps": {}}
    # 环境先落地：注入被拦时，「在谁的进程树/哪个桌面里跑的」就是结论本身
    report["environment"] = environment_facts()
    client = ExtensionExecClient()
    status = client.status()
    report["extension"] = {
        "online": bool(status.get("online")),
        "endpoint": status.get("host", {}).get("endpoint"),
        "extVersion": status.get("host", {}).get("extVersion"),
    }
    if not status.get("online"):
        report["fatal"] = "扩展不在线"
        return emit(report, 2, "扩展不在线：先在浏览器里打开扩展弹窗确认在线")

    handler = __import__("functools").partial(_QuietHandler, directory=str(TESTAPPS_BROWSER))
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{server.server_port}/basic.html"
    report["pageUrl"] = page_url

    executor = PlaywrightExecutor(ext_session=ExtensionExecSession(client=client))
    session_id = None
    try:
        nav = run(
            executor,
            "browser.navigate",
            {"browserType": "msedge", "action": "goto", "url": page_url},
            "nav",
        )
        if nav.status != "success":
            report["fatal"] = {"reason": "navigate 失败", "detail": brief(nav)}
            return emit(report, 1, f"navigate 失败：{nav.error}")
        session_id = nav.outputs["sessionId"]
        report["sessionId"] = session_id
        time.sleep(0.8)  # 等 target 激活 + 首帧渲染

        for script, key in ((DRESS_UP, "dress"), (INSTALL_PROBE, "install")):
            out = run(
                executor,
                "browser.executeScript",
                {"sessionId": session_id, "script": script},
                key,
            )
            if out.status != "success":
                report["fatal"] = {"reason": f"{key} 失败", "detail": brief(out)}
                return emit(report, 1, f"{key} 失败：{out.error}")
            # 脚本自己会回 'no-target'：页面没渲染出按钮时不能静默往下走
            if out.outputs.get("result") == "no-target":
                report["fatal"] = f"{key}: 页面里找不到 {TARGET}"
                return emit(report, 1, report["fatal"])

        geo = run(
            executor,
            "browser.executeScript",
            {"sessionId": session_id, "script": READ_GEOMETRY},
            "geometry",
        )
        if geo.status != "success":
            report["fatal"] = {"reason": "取几何失败", "detail": brief(geo)}
            return emit(report, 1, f"取几何失败：{geo.error}")
        geometry = geo.outputs.get("result") or {}
        report["geometry"] = geometry
        rect = geometry.get("rect") or {}
        viewport = geometry.get("viewport") or {}

        # 1) 运行时视口截图（产品命令，落盘到临时文件）
        shot_path = ROOT / ".harness" / "spike" / "_m107_viewport.png"
        shot = run(
            executor,
            "browser.screenshot",
            {"sessionId": session_id, "savePath": str(shot_path)},
            "screenshot",
        )
        report["steps"]["screenshot"] = brief(shot)
        if shot.status != "success":
            return emit(report, 1, f"视口截图失败：{shot.error}")

        # 2) 裁模板
        cropped = crop_template(shot_path, rect, viewport)
        if not cropped:
            report["fatal"] = "裁模板失败（视口几何不足）"
            return emit(report, 1, report["fatal"])
        template_png, crop_meta = cropped
        report["steps"]["template"] = crop_meta
        template_path = ROOT / ".harness" / "spike" / "_m107_template.png"
        template_path.write_bytes(template_png)

        # 3) 找浏览器窗口（imageLocate 需要窗口句柄截窗口图）
        hwnd = find_window_by_title(PAGE_TITLE)
        report["windowHandle"] = hwnd
        if not hwnd:
            report["fatal"] = "没找到靶页窗口（标题里没有靶页 title）"
            return emit(report, 1, report["fatal"])

        report["foreground"] = ensure_foreground(hwnd)
        time.sleep(0.4)

        # 4) 图像定位（产品命令，模板匹配 → 屏幕绝对矩形）
        locate = run(
            DesktopImageExecutor(),
            "desktop.imageLocate",
            {
                "windowHandle": hwnd,
                "imageTemplate": base64.b64encode(template_png).decode("ascii"),
                "templateSize": list(crop_meta["cropSize"]),
                "minConfidence": 0.7,
            },
            "imageLocate",
        )
        report["steps"]["imageLocate"] = brief(locate)
        if locate.status != "success":
            return emit(report, 1, f"图像定位失败：{locate.error}")
        out = locate.outputs
        point = (int(out["left"] + out["width"] // 2), int(out["top"] + out["height"] // 2))
        report["imageRect"] = {k: out[k] for k in ("left", "top", "width", "height", "count")}
        report["clickPoint"] = list(point)

        # 4a) 事实清单 + 标记图：坐标到底对不对，先留可复核的证据
        report["windowFacts"] = window_facts(hwnd, point, rect)
        report["markedShot"] = save_marked_window(hwnd, report["imageRect"], point)

        # 4b) 注入自检：光标不跟着走 ⇒ 点击根本没发生（UIPI / 安全软件拦注入）
        pre = preflight_injection()
        report["injection"] = pre
        if not pre["moved"]:
            env = report.get("environment") or {}
            both_dead = pre["setCursorPos"].get("ok") is False
            downgraded = env.get("inheritedIntegrity") is False and env.get("canInject") is False
            if downgraded:
                conclusion = (
                    f"BLOCKED 本进程完整性={env.get('integrityName')}（{env.get('integrity')}），"
                    f"父进程却是 {env.get('parentIntegrityName')}（{env.get('parentIntegrity')}）"
                    "——令牌不是继承来的，是**创建进程时被降权的**，注入不可能发生。"
                    "查 EDR/沙箱的「可疑进程降权」策略，或在允许注入的机器上跑"
                )
            elif env.get("canInject") is False:
                conclusion = (
                    f"BLOCKED 本进程完整性={env.get('integrity')}（{env.get('integrityName')}）："
                    "实测 SendInput 与 SetCursorPos 双双无效 ⇒ 注入不可能发生。"
                    "换 Medium/High 完整性的终端再跑"
                    "（whoami /groups | findstr Label 应显示 Medium），这不是坐标问题"
                )
            elif both_dead:
                conclusion = (
                    "BLOCKED SendInput 与 SetCursorPos 双双无效（完整性正常时多为 EDR/策略拦截），"
                    "不是坐标问题"
                )
            else:
                conclusion = (
                    "BLOCKED SendInput 无效但 SetCursorPos 可用 ⇒ 注入参数/结构问题，不是环境"
                )
            report["verdict"] = {
                "eventCount": 0,
                "clickCount": 0,
                "trustedClicks": 0,
                "clickLanded": False,
                "conclusion": conclusion,
            }
            return emit(report, 3, conclusion)

        # 4c) 先让页面自己回答「这一点是不是落在按钮上」（:hover 不依赖任何换算）
        from rpa_core.executors import human_input as _hi

        report["hoverMove"] = _hi.move_to(int(point[0]), int(point[1]))
        time.sleep(0.35)
        hover = run(
            executor,
            "browser.executeScript",
            {"sessionId": session_id, "script": HOVER_CHAIN},
            "hover",
        )
        chain = hover.outputs.get("result") if hover.status == "success" else None
        report["hoverChain"] = chain
        report["hoverOnTarget"] = bool(chain) and any("#btn" in str(item) for item in chain)
        report["cursorBeforeClick"] = cursor_now()
        report["foregroundBeforeClick"] = foreground_now(hwnd)

        # 5) 物理点击（真鼠标：SendInput + 缓动轨迹）
        from rpa_core.executors.image_target import perform_click

        perform_click(point, click_type="single", button="left")
        time.sleep(0.6)
        report["cursorAfterClick"] = cursor_now()

        # 6) 读回页面观察到的 isTrusted
        probe = run(
            executor,
            "browser.executeScript",
            {"sessionId": session_id, "script": READ_PROBE},
            "readProbe",
        )
        probe_result = probe.outputs.get("result") if probe.status == "success" else None
        report["observedEvents"] = (probe_result or {}).get("events")

        text = run(
            executor,
            "browser.getText",
            {"sessionId": session_id, "selector": STATUS, "infoType": "text"},
            "readStatus",
        )
        report["statusText"] = text.outputs.get("value") if text.status == "success" else None
    finally:
        if session_id:
            # 先把标签页导航走：``browser.close`` 是**解绑**（标签页留给用户），
            # 连跑几轮就会在窗口里留下好几个同标题的靶页——下一次
            # ``find_window_by_title`` 可能挑中别的窗口，白跑一轮。
            try:
                run(
                    executor,
                    "browser.navigate",
                    {"sessionId": session_id, "action": "goto", "url": "about:blank"},
                    "park",
                )
            except Exception:  # noqa: BLE001 - 收尾失败不该掩盖验证结果
                pass
            try:
                run(executor, "browser.close", {"sessionId": session_id}, "close")
            except Exception as exc:  # noqa: BLE001 - 收尾失败不该掩盖验证结果
                report["closeError"] = f"{type(exc).__name__}: {exc}"
        server.shutdown()
        server.server_close()

    # -- 判定 ---------------------------------------------------------------
    events = report.get("observedEvents") or []
    clicks = [e for e in events if e.get("type") == "click"]
    trusted = [e for e in clicks if e.get("isTrusted") is True]
    landed = report.get("statusText") == "clicked:1"
    on_target = report.get("hoverOnTarget")
    if trusted and landed:
        conclusion = "PASS 物理点击产出 isTrusted=true 且落在目标上"
    elif on_target is False:
        conclusion = "FAIL 光标没落在按钮上（坐标/匹配偏了，看 markedShot 红框与绿十字）"
    elif on_target and not events:
        conclusion = "FAIL 光标在按钮上但页面零事件：点击没送达（前台/焦点/输入被吞）"
    else:
        conclusion = "FAIL 见 observedEvents / statusText"
    report["verdict"] = {
        "eventCount": len(events),
        "clickCount": len(clicks),
        "trustedClicks": len(trusted),
        "clickLanded": landed,
        "hoverOnTarget": on_target,
        "conclusion": conclusion,
    }
    return emit(report, 0 if (trusted and landed) else 1, report["verdict"]["conclusion"])


if __name__ == "__main__":
    raise SystemExit(main())
