"""前台浏览器窗口识别（M47.9：校验目标改用「最近激活的浏览器」）。

## 为什么需要它

维护者口径（对齐影刀）：
*「影刀校验时只看哪个浏览器是**最后被激活的**；若对话框的上一个激活窗口不是浏览器，
它会把这个浏览器拉到前台再高亮。」*
实测规格：依次点 **edge → chrome → 资源管理器**，再点校验 ⇒ 影刀**把 chrome 置前**并高亮。

旧实现（M47.7/M47.8）把 `capture_verify` **广播**给全部扩展端点，靠每个浏览器自判
「我现在是否前台」再让首个回传者胜。它答不出「**最近**激活的浏览器是谁」——
点校验时前台往往是资源管理器或我们自己的对话框，于是两个浏览器都判非前台 ⇒ **都不闪**。

## 系统已经有这个「栈」，不用自己维护

Windows 的 **Z 序**就是系统维护的窗口激活栈：

- ``GetTopWindow(NULL)`` = 栈顶（最靠前 / 最近激活）；
- ``GetWindow(hwnd, GW_HWNDNEXT)`` 沿链向下 = 由近到远的激活顺序；
- ``EnumWindows`` 返回与之同序（实测前 12 个逐一致）。

于是**「最近被激活的浏览器」= Z 序链里第一个浏览器类窗口**——非浏览器窗口（资源管理器、
我们的对话框、系统提示窗）自然被跳过。无需轮询、无跨进程时钟、无竞态。

## 实测必须做的过滤

``GetTopWindow`` 的第一项**可能不是用户窗口**（实测拿到过 ``tooltips_class32`` 空提示窗），
所以逐项过滤：``IsWindowVisible`` 且标题非空 且无 ``WS_EX_TOOLWINDOW``。

## 落点

本模块**只读、无副作用**（除可选置前，见 :func:`bring_to_foreground`）。
真正的 Windows 调用收在 :class:`_Win32Windows` 里，可注入替身 ⇒ 纯逻辑在任意平台可单测。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

from rpa_core.capture._trace import trace as _trace

# 浏览器窗口类名（**仅作文档参考**，不用于判定）：Chrome / Edge / Brave / Opera / Vivaldi
# 都是 ``Chrome_WidgetWin_1``，Firefox 是 ``MozillaWindowClass``。类名区分不出是哪家，
# 也不能证明「是浏览器」——定制 Chromium 客户端（咚咚/WorkBuddy…）同样用这个类名，
# 所以判定一律走进程名（见 _is_browser_window）。

# 进程名 → 浏览器标识（与 endpoint 命名里的 <browser> 段一致，见 extension_exec）。
_PROCESS_BROWSERS: dict[str, str] = {
    "msedge.exe": "msedge",
    "chrome.exe": "chrome",
    "brave.exe": "brave",
    "opera.exe": "opera",
    "vivaldi.exe": "vivaldi",
    "firefox.exe": "firefox",
}

_WS_EX_TOOLWINDOW = 0x00000080

# 桌面壳窗口：可见且有标题，但不是任何校验目标。
_SHELL_WINDOW_CLASSES = {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}


@dataclass(frozen=True)
class ForegroundBrowser:
    """Z 序里第一个浏览器窗口。"""

    hwnd: int
    browser: str          # msedge / chrome / ...（与 endpoint <browser> 段同词表）
    process: str          # 进程可执行名，如 msedge.exe（诊断用）
    title: str            # 窗口标题（诊断用，不含页面内容以外的东西）


class _WindowsAPI(Protocol):
    """win32 调用接缝：生产用 :class:`_Win32Windows`，测试注入替身。"""

    def enumerate_top_windows(self) -> list[tuple[int, str, str, str, bool]]:
        """按 Z 序（前 = 最靠前）返回 ``(hwnd, class_name, title, process, visible)``。

        实现方**不做过滤**——过滤规则属业务逻辑，留在 :func:`first_browser_window`
        里以便单测。``process`` 取不到时给空串。
        """
        ...

    def foreground(self) -> int:
        """当前前台窗口句柄（取不到给 0）。"""
        ...

    def set_foreground(self, hwnd: int) -> bool:
        """尝试把窗口置前，返回是否成功（平台限制见 :func:`bring_to_foreground`）。"""
        ...


def _is_browser_window(class_name: str, process: str) -> str | None:
    """窗口是否浏览器主窗，是则返回浏览器标识（msedge/chrome/...），否则 None。

    **只看进程名**：``Chrome_WidgetWin_1`` 这个类名不只 Chrome/Edge 用——实测本机还有
    ``咚咚.exe`` / ``WorkBuddy.exe`` 这类**基于 Chromium 的定制客户端**也用同一类名，
    它们不是可下发命令的浏览器（没有我们的扩展、也认不出是哪个 browser 段）。
    所以**不能**「类名是 Chromium 系就认」，必须进程名命中已知浏览器表。
    认不出时返回 None（跳过这一项），让 Z 序继续往下找真正的浏览器。
    """
    name = str(process or "").strip().lower()
    return _PROCESS_BROWSERS.get(name)


def _is_user_window(class_name: str, title: str, visible: bool) -> bool:
    """Z 序项是否「用户看得见的窗口」。

    实测 ``GetTopWindow`` 链里混着 ``tooltips_class32`` / ``ForegroundStaging`` /
    ``IME`` 等系统窗与工具窗，必须先滤掉，否则「第一个浏览器」会被它们挤掉或误判。
    桌面壳（``Progman`` / ``WorkerW``）虽然「可见 + 有标题」，但从不是校验目标，也滤掉。
    """
    if not visible:
        return False
    if not str(title or "").strip():
        return False
    return str(class_name or "").strip().lower() not in _SHELL_WINDOW_CLASSES


def first_browser_window(
    api: _WindowsAPI | None = None,
) -> ForegroundBrowser | None:
    """Z 序链里第一个**浏览器**窗口 = 「最近激活的浏览器」；没有则 None。

    非 Windows 或 API 不可用时返回 None（调用方退回旧的广播行为）。
    """
    api = api or _default_api()
    if api is None:
        return None
    try:
        windows = api.enumerate_top_windows()
    except Exception:  # noqa: BLE001 - 探测失败绝不影响校验主流程
        return None
    for hwnd, class_name, title, process, visible in windows:
        if not _is_user_window(class_name, title, visible):
            continue
        browser = _is_browser_window(class_name, process)
        if browser:
            return ForegroundBrowser(
                hwnd=int(hwnd), browser=browser, process=process, title=title,
            )
    return None


def bring_to_foreground(hwnd: int, api: _WindowsAPI | None = None) -> bool:
    """尝试把 ``hwnd`` 置前（影刀「把浏览器拉到前台」那步）。返回是否成功。

    **平台限制**：Windows 只允许「当前前台进程」调用 ``SetForegroundWindow`` 换前台，
    后台进程的调用会被拒（或只闪任务栏）。本项目的 GUI 在点「校验元素」时**正是前台**
    （对话框抢焦），所以有较大概率成功；失败不抛、只记 trace，调用方据此决定是否提示。
    """
    api = api or _default_api()
    if api is None or not hwnd:
        return False
    try:
        ok = bool(api.set_foreground(int(hwnd)))
    except Exception:  # noqa: BLE001 - 置前失败属预期（前台锁定），不该冒泡
        ok = False
    _trace("verify", "bring_to_foreground", hwnd=int(hwnd), ok=ok)
    return ok


# ---------------------------------------------------------------- 生产实现


class _Win32Windows:
    """基于 ``ctypes.windll.user32`` 的真实实现（仅 Windows 可构造）。"""

    _GW_HWNDNEXT = 2
    _GWL_EXSTYLE = -20
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        # 64 位陷阱：句柄 API 不设 restype/argtypes 会按 32 位截断（DefWindowProcW 教训）
        u.GetTopWindow.argtypes = [wintypes.HWND]
        u.GetTopWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._u = u
        self._k = k

    def _process_name(self, hwnd: int) -> str:
        pid = self._wintypes.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = self._k.OpenProcess(self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = self._wintypes.DWORD(260)
            buf = self._ctypes.create_unicode_buffer(260)
            if self._k.QueryFullProcessImageNameW(handle, 0, buf, self._ctypes.byref(size)):
                return str(buf.value).rsplit("\\", 1)[-1]
            return ""
        finally:
            self._k.CloseHandle(handle)

    def enumerate_top_windows(self) -> list[tuple[int, str, str, str, bool]]:
        out: list[tuple[int, str, str, str, bool]] = []
        hwnd = int(self._u.GetTopWindow(0) or 0)
        seen = 0
        # 上限防御：链异常时不死循环。**实测该链可达 400+ 项**（QQ 拼音/IME/工具提示等
        # 系统窗密布），真正的用户窗口排在第 100~200 位——上限取 2048 留足余量。
        while hwnd and seen < 2048:
            seen += 1
            if not self._u.GetWindowLongW(hwnd, self._GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
                cls = self._ctypes.create_unicode_buffer(256)
                self._u.GetClassNameW(hwnd, cls, 256)
                text = self._ctypes.create_unicode_buffer(512)
                self._u.GetWindowTextW(hwnd, text, 512)
                out.append((
                    hwnd,
                    cls.value,
                    text.value,
                    self._process_name(hwnd),
                    bool(self._u.IsWindowVisible(hwnd)),
                ))
            hwnd = int(self._u.GetWindow(hwnd, self._GW_HWNDNEXT) or 0)
        return out

    def foreground(self) -> int:
        return int(self._u.GetForegroundWindow() or 0)

    def set_foreground(self, hwnd: int) -> bool:
        return bool(self._u.SetForegroundWindow(hwnd))


_DEFAULT_API: _WindowsAPI | None | bool = False   # False = 尚未初始化


def _default_api() -> _WindowsAPI | None:
    """惰性构造默认实现；非 Windows 恒 None（调用方据此退回旧行为）。"""
    global _DEFAULT_API
    if _DEFAULT_API is False:
        if sys.platform == "win32":
            try:
                _DEFAULT_API = _Win32Windows()
            except Exception:  # noqa: BLE001 - 构造失败当作无能力
                _DEFAULT_API = None
        else:
            _DEFAULT_API = None
    return _DEFAULT_API  # type: ignore[return-value]
