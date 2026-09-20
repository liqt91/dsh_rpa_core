"""GUI 诊断日志（仅排障用）。

默认**完全关闭**（零开销）：设 ``RPA_GUI_DEBUG=1`` 后写入日志文件，供复现
「拖放后点击不选中」这类视图/选中态问题时观察事件顺序与状态。

用法：
    PowerShell:  $env:RPA_GUI_DEBUG="1"; uv run python -m rpa_core.cli gui
    bash:        RPA_GUI_DEBUG=1 uv run python -m rpa_core.cli gui

日志文件：``%TEMP%\\rpa_gui_debug.log``（可用 ``RPA_GUI_DEBUG_FILE`` 覆盖）。
每次启动会**清空**旧日志，便于只看本轮复现。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

_VALUE = os.environ.get("RPA_GUI_DEBUG", "")
ENABLED = _VALUE not in ("", "0", "false", "False")
# 也允许把日志路径直接写进 RPA_GUI_DEBUG（等价于设 RPA_GUI_DEBUG_FILE）
if ENABLED and _VALUE not in ("1", "true", "True"):
    os.environ.setdefault("RPA_GUI_DEBUG_FILE", _VALUE)


def _log_path() -> Path:
    override = os.environ.get("RPA_GUI_DEBUG_FILE")
    if override:
        return Path(override)
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    return Path(temp) / "rpa_gui_debug.log"


_LOCK = threading.Lock()
_initialized = False


def log(event: str, **fields: object) -> None:
    """写一行诊断日志（未启用时立即返回）。"""
    if not ENABLED:
        return
    global _initialized
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    parts = " ".join(f"{key}={value!r}" for key, value in fields.items())
    with _LOCK:
        path = _log_path()
        if not _initialized:
            _initialized = True
            try:
                path.write_text(
                    f"# rpa_gui_debug pid={os.getpid()} {datetime.now():%Y-%m-%d %H:%M:%S}\n",
                    encoding="utf-8",
                )
            except OSError:
                return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {event} {parts}\n")
        except OSError:
            return


def log_path() -> str:
    """日志文件路径（供界面/终端提示）。"""
    return str(_log_path())


def init() -> None:
    """启用时立刻建文件并写头行，便于确认日志确实开着。"""
    if ENABLED:
        log("init", file=str(_log_path()))


def install_window_show_watch(app) -> None:
    """调试：记录「窗口级控件被显示」事件（含调用栈）。

    用于定位「切换指令时小框/窗口闪现」这类瞬时原生窗口——菜单、工具提示、
    弹出视图都是顶层窗口，被显示时必然走 Show 事件；这里把类名、尺寸、位置
    与调用栈写进诊断日志，复现一次即可确定是谁弹的。仅在 ``RPA_GUI_DEBUG``
    启用时安装（默认零开销）。
    """
    if not ENABLED:
        return
    import traceback

    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget

    class _WindowShowWatcher(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
            try:
                if (
                    event.type() == QEvent.Type.Show
                    and isinstance(obj, QWidget)
                    and obj.isWindow()
                ):
                    stack = " | ".join(
                        f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}"
                        for frame in traceback.extract_stack()[-8:]
                    )
                    log(
                        "window-show",
                        cls=type(obj).__name__,
                        size=(obj.width(), obj.height()),
                        pos=(obj.x(), obj.y()),
                        stack=stack,
                    )
            except Exception:  # noqa: BLE001 - 诊断本身绝不能影响 GUI
                pass
            return False

    watcher = _WindowShowWatcher(app)
    app.installEventFilter(watcher)
    app._rpa_window_show_watcher = watcher  # 防被 GC
