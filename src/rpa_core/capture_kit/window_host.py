"""宿主窗口的「最小化 → 还原」状态适配。

ppx 用的 pywebview(WinForms) ``restore()`` 直接把 ``WindowState`` 设为 Normal：
最小化前主窗若是**最大化**，捕获/编辑结束后就被还原成普通大小，用户看到
「RPA 窗口自己缩了」（2026-11-19 现场）。

这里把「记住最小化前的状态、还原时按原状态恢复」做成不依赖 webview / win32
的纯适配层；原生状态探针由宿主注入，契约测试可以完整覆盖状态机。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MaximizedAwareHost:
    """包一层宿主：minimize 前探测最大化状态，restore 时按原状态恢复。

    ``probe`` 返回 True 表示**调用 minimize 的那一刻**主窗处于最大化；拿不到
    状态（非 Windows / 探针异常）应返回 False，退回原来的 ``restore()`` 行为。

    状态在 ``restore()`` 后清零：同一实例可能被复用，不能让上一次的最大化
    状态泄漏到下一次捕获。
    """

    def __init__(self, host: Any, probe: Callable[[], bool]) -> None:
        self._host = host
        self._probe = probe
        self._was_maximized = False

    def minimize(self) -> None:
        # 必须先探测再最小化：最小化之后 IsZoomed 必然是 False。
        self._was_maximized = bool(self._probe())
        self._host.minimize()

    def restore(self) -> None:
        if self._was_maximized:
            self._host.maximize()
        else:
            self._host.restore()
        self._was_maximized = False


__all__ = ["MaximizedAwareHost"]
