"""桌面元素活体校验（M74 S3/S4）：跨窗口找元素 + 命中置前。

与 Web 的 ``ElementVerifier`` 对称，但信息源是 UIA/Win32：**不另建定位权威**——
现场查找一律调 ``DesktopExecutor._find``（``executors/desktop.py``），窗口构造走
``_window_by_handle``。本模块只做编排：

1. 按 Z 序（``capture.foreground_window.find_window_for_element``）遍历所有可见顶层窗口；
2. 每个窗口用元素的 ``selector.locator`` 现场 ``_find``，第一个命中即停；
3. 命中回传统一形状（``count`` / ``windowHandle`` / ``rect`` / ``error``）；
4. ``flash`` 模式把**命中的窗口**置前（``bring_to_foreground``），``preview``/``clear``
   不抢焦点，置前失败吞异常只 trace——与 Web 三 mode 口径逐字对齐。

## 可测性

真实 win32/UIA 只在 ``_default_window_factory`` / ``_default_finder`` 里；测试注入替身
即可在任意平台证明「搜索顺序、命中即停、三 mode 置前、错误三态」，不依赖真机窗口。
"""

from __future__ import annotations

from typing import Any

from rpa_core.capture._trace import trace as _trace
from rpa_core.capture.foreground_window import (
    TopWindow,
    _WindowsAPI,
    find_window_for_element,
)

_VALID_MODES = ("flash", "preview", "clear")


def _default_window_factory(hwnd: int) -> Any | None:
    """生产实现：从 HWND 直接构造 UIAWrapper（复用执行器的唯一路径）。"""
    from rpa_core.executors.desktop import DesktopExecutor

    return DesktopExecutor._window_by_handle(hwnd)


def _default_finder(window: Any, locator: Any) -> list[Any]:
    """生产实现：现场 ``_find``（复用执行器的唯一定位权威）。

    ★ 元素资产里存的是 JSON 字典，``DesktopExecutor._find`` 吃的是 ``DesktopLocator``
    模型。这一步归一化必须在**执行层门口**做：替身 finder 的契约测试看不到它，
    真机一旦漏掉就会在每个窗口抛 ``AttributeError: 'dict' has no attribute 'anchor'``，
    最后被误报成 ``find-failed``（M79 真机实锤）。
    """
    from rpa_core.executors.desktop import DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    resolved = DesktopLocator.model_validate(locator) if isinstance(locator, dict) else locator
    return DesktopExecutor._find(window, resolved)


def _default_waker(hwnd: int) -> Any | None:
    """生产实现：**在第一次 UIA 查询之前**尝试唤醒 Qt 应用的休眠树（M100）。

    与捕获路径同一个 ``accessibility.activate``（唯一写 gate 的入口），区别只是
    这里没有 hover 循环的节流状态。返回 ``None`` = 不是休眠候选（原生窗口不碰）。
    """
    from rpa_core.capture import accessibility

    return accessibility.wake_if_dormant(int(hwnd))


def _default_tree_count(window: Any) -> int:
    """数一个窗口里的 UIA 后代（诊断「树建没建起来」）。

    只数一步，不做递归遍历——调用方只在 ``count=0`` 且窗口像 Qt 壳时才问它。
    """
    return len(window.descendants())


def _rect_of(match: Any) -> dict[str, int] | None:
    """把 pywinauto 的 rectangle 转成与 Web 对齐的 ``{left,top,width,height}``。"""
    try:
        rect = match.rectangle()
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(rect.right - rect.left),
            "height": int(rect.bottom - rect.top),
        }
    except Exception:  # noqa: BLE001 - 高亮/预览是观感层，取不到框不该让校验失败
        return None


class DesktopElementVerifier:
    """桌面元素校验器：``verify``/``preview``/``clear_preview`` 三 mode。

    构造参数全部可注入（默认走生产实现）：``window_factory(hwnd)``、
    ``finder(window, locator)``、``api``（Z 序替身）、``bring_front(hwnd)``、
    ``waker(hwnd)``（查前唤醒）、``tree_count(window)``（命中 0 时的树诊断）。
    """

    def __init__(
        self,
        *,
        window_factory: Any = None,
        finder: Any = None,
        api: _WindowsAPI | None = None,
        bring_front: Any = None,
        expected_hwnd: int | None = None,
        highlighter: Any = None,
        shot_fn: Any = None,
        waker: Any = None,
        tree_count: Any = None,
    ):
        self._window_factory = window_factory or _default_window_factory
        self._finder = finder or _default_finder
        self._api = api
        self._bring_front = bring_front
        self._expected_hwnd = expected_hwnd
        self._highlighter = highlighter
        self._shot_fn = shot_fn
        self._waker = waker or _default_waker
        self._tree_count = tree_count or _default_tree_count

    def _wake_before_query(self, window: TopWindow) -> None:
        """把候选窗口叫醒（在它被第一次 UIA 查询之前）。

        只记「真试过且不顺利」的情形：``None``（不是休眠候选）是绝大多数窗口的
        常态，每秒记一条会把 trace 埋掉。
        """
        try:
            result = self._waker(window.hwnd)
        except Exception as exc:  # noqa: BLE001 - 唤醒是增强路径，失败不打断校验
            _trace("desktop_verify", "wake_error", hwnd=window.hwnd, error=type(exc).__name__)
            return
        if result is None:
            return
        if not getattr(result, "ok", False) or getattr(result, "poked", None) is False:
            _trace(
                "desktop_verify", "wake",
                hwnd=window.hwnd, ok=getattr(result, "ok", False),
                reason=getattr(result, "reason", ""), poked=getattr(result, "poked", None),
            )

    def _tree_missing(self, checked: list[TopWindow]) -> bool:
        """查过一圈都没命中时，判断「是不是对方根本没有无障碍树」。

        只在窗口**类名像 Qt 壳**时才去数后代——原生窗口（记事本、Chromium）本来就
        可能只有一两个节点，不能拿它报「树没建起来」。
        """
        from rpa_core.capture import accessibility

        for window in checked:
            if not accessibility.looks_dormant(window.class_name, 1):
                continue
            try:
                if self._tree_count(window) > 1:
                    continue  # 树是活的，只是选择器没匹配上
            except Exception:  # noqa: BLE001 - 数不出来就不下这个结论
                continue
            return True
        return False

    def _find_across_windows(
        self, locator: Any
    ) -> tuple[Any | None, TopWindow | None, str | None, list[TopWindow]]:
        """跨窗口查一个 locator，返回 ``(match, window, error, checked)``。

        probe 里捕获定位异常（COM/锚点未解析/非法 locator）转成结构化 error，
        **不让异常越过 mode 层**——GUI 只认 error 字段，不接抛出的栈。

        ``checked`` 是真正 probe 过的窗口（按 Z 序），供命中 0 时的树诊断使用。
        """
        from rpa_core.executors.desktop import AnchorNotResolved

        captured: dict[str, Any] = {}
        errors: list[str] = []
        probed_ok = 0
        checked: list[TopWindow] = []

        def probe(window: TopWindow) -> bool:
            nonlocal probed_ok
            checked.append(window)
            try:
                wrapper = self._window_factory(window.hwnd)
                if wrapper is None:
                    return False
                matches = self._finder(wrapper, locator)
            except AnchorNotResolved:
                errors.append("anchor-not-found")
                return False
            except Exception as exc:  # noqa: BLE001 - 单窗 COM 失败不打断整轮跨窗口搜索
                _trace("desktop_verify", "find_error", hwnd=window.hwnd, error=type(exc).__name__)
                errors.append("find-failed")
                return False
            probed_ok += 1
            if matches:
                captured["match"] = matches[0]
                return True
            return False

        # M95：任务栏（Shell_TrayWnd / Shell_SecondaryTrayWnd）没有标题，默认
        # 窗口链会把它当系统窗滤掉，于是「开始图标右侧的搜索按钮」永远查不到。
        # 桌面元素校验需要按 Z 序把它放回链里；浏览器路径不传这个开关。
        hit_window = find_window_for_element(
            probe, self._api, preferred_hwnd=self._expected_hwnd,
            include_desktop_shell=True,
            # ★ 唤醒必须发生在每次 probe（= 第一次 UIA 查询）之前：微信 4.x 的树
            #   只在进程首次被查询前建得起来（M98）。
            before_probe=self._wake_before_query,
        )
        if hit_window is not None:
            return captured.get("match"), hit_window, None, checked
        # 只有「一个窗口都没能成功查过」才算通道错误；部分窗口失败但其它窗口
        # 正常查过且没命中，是 count=0，不是错误（真机 WorkerW/无 provider 窗口常态）。
        if errors and probed_ok == 0:
            return None, None, errors[0], checked
        return None, None, None, checked

    def _run(self, locator: Any, mode: str, want_shot: bool = False) -> dict[str, Any]:
        if mode not in _VALID_MODES:
            return {"error": "unsupported-mode"}

        # clear：只清场（桌面没有持续高亮状态可清），不查也不抢焦点。
        if mode == "clear":
            return {"count": 0}

        match, window, error, checked = self._find_across_windows(locator)
        if error:
            return {"error": error}
        if match is None or window is None:
            empty: dict[str, Any] = {"count": 0}
            # 只有用户主动点「校验」才做树诊断：preview 是边打字边跑的，
            # 每敲一个字多一轮 UIA 查询不值当。
            if mode == "flash" and checked and self._tree_missing(checked):
                empty["hint"] = "tree-missing"
                _trace(
                    "desktop_verify", "tree_missing",
                    windows=[w.hwnd for w in checked],
                )
            return empty

        result: dict[str, Any] = {"count": 1, "windowHandle": window.hwnd}
        rect = _rect_of(match)
        if rect is not None:
            result["rect"] = rect
        # 影刀那步②（S4）：只有用户明确点的 flash 才置前**命中的窗口**并画一次红框；
        # preview 边打字边预览、clear 收场都不该抢焦点/不该留框。失败吞异常。
        if mode == "flash":
            self._bring_front_to(window.hwnd)
            if rect is not None:
                self._flash(rect)
        if want_shot:
            shot = self._shot(window.hwnd, rect)
            if shot:
                result["shot"] = shot
            _trace(
                "desktop_verify", "shot", mode=mode, hwnd=window.hwnd,
                has_rect=rect is not None, has_shot=bool(shot),
            )
        return result

    def _flash(self, rect: dict[str, Any]) -> None:
        flash = self._highlighter
        if flash is None:
            from rpa_core.capture.desktop_highlight import flash_rect

            flash = flash_rect
        try:
            flash(rect)
        except Exception:  # noqa: BLE001 - 高亮是观感层，失败绝不影响校验结果
            pass

    def _shot(self, hwnd: int, rect: dict[str, Any] | None) -> dict[str, Any] | None:
        shot_fn = self._shot_fn
        if shot_fn is None:
            from rpa_core.capture.screen_shot import shot_for_desktop

            shot_fn = shot_for_desktop
        try:
            return shot_fn(hwnd, rect)
        except Exception:  # noqa: BLE001 - 截图失败不影响命中数（同 Web 口径）
            return None

    def _bring_front_to(self, hwnd: int) -> None:
        bring = self._bring_front
        if bring is None:
            from rpa_core.capture.foreground_window import bring_to_foreground

            bring = bring_to_foreground
        try:
            bring(hwnd)
        except Exception:  # noqa: BLE001 - 置前是观感优化，失败绝不影响校验结果
            pass

    def verify(self, locator: Any, *, want_shot: bool = False) -> dict[str, Any]:
        """点「校验元素」：跨窗口找 + flash 命中窗口置前。"""
        return self._run(locator, "flash", want_shot=want_shot)

    def preview(self, locator: Any) -> dict[str, Any]:
        """编辑中即时预览：**不置前**（不抢用户焦点）。"""
        return self._run(locator, "preview")

    def clear_preview(self) -> dict[str, Any]:
        """清场：不查不置前。"""
        return self._run(None, "clear")


__all__ = ["DesktopElementVerifier"]
