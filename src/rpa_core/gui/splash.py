"""启动加载提示（M41）：主窗口出现前盖住初始化等待，避免「敲了命令没反应」。

为什么需要：``run_gui`` 里 ``load_catalog`` + Qt 初始化 + 首个窗口构造这段是**同步阻塞**的，
主窗口要等它们跑完才出现。真机分段实测（``.harness/spike/probe_m41_startup.py``，冷启动）：

    load_catalog(commands/)    420.5 ms   ← 首次含惰性 import（pydantic 等）
    build_application()        275.8 ms   ← QApplication 构造 + Qt 平台插件加载
    HomeWindow(...) 构造        68.5 ms
    home.show() + 首帧         211.3 ms
    -------------------------------------
    窗口出现前                 ≈ 976 ms

冷启动接近一秒的白等，用户会以为命令没生效（维护者 2026-09-28：「启动前如果要初始化，
可以有个加载提示」）。本模块提供一个在场约一秒的卡片窗口，把这段变成「有反馈的等待」。

行为约定（三条都有测试钉住）：

- **不抢焦点**：``Qt.SplashScreen`` 自带 ``WindowDoesNotAcceptFocus``，再加
  ``WA_ShowWithoutActivating``——初始化期间用户敲的键不该被它吃掉；
- **必须主动泵一轮事件**：初始化是同步阻塞的，``show()`` 之后不 ``processEvents()``
  的话窗口要等初始化结束才被绘制，那就完全失去意义（首帧 211 ms 正是这段）；
- **异常路径也要撤**：``startup_splash()`` 是上下文管理器，初始化抛异常时 ``finally``
  关窗，不让卡片留在屏幕上挡住真正的报错。

观感与 ``run_float`` / ``capture_float`` 一致（无边框 + 自绘卡片底）：三者是同一类
「主窗口之外的在场小窗」。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from rpa_core.gui import fonts
from rpa_core.gui.theme import (
    BORDER,
    SURFACE,
    TEXT_MUTED,
)

_WIDTH = 320
_HEIGHT = 128
_NEUTRAL = TEXT_MUTED


class StartupSplash(QWidget):
    """启动加载卡片：产品名 + 「正在初始化…」。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # SplashScreen = 无边框 + 不占任务栏；**不抢焦点必须显式声明**——
        # 实测（tests/contract/test_gui_splash.py）PySide6 的 Qt.WindowType.SplashScreen
        # 枚举值里并不含 WindowDoesNotAcceptFocus（老代码注释常写「自带」，那是 Qt5 印象）。
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(_WIDTH, _HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("rpa-core")
        title.setStyleSheet(f"font-size: {fonts.TITLE_PX}px; font-weight: 600;")
        subtitle = QLabel("正在初始化…")
        subtitle.setStyleSheet(f"color: {_NEUTRAL};")
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)

    def place_center(self) -> None:
        """居中到主屏幕：启动时还没有可参照的父窗口。"""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.center().x() - _WIDTH // 2,
            area.center().y() - _HEIGHT // 2,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆写
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(BORDER)))
        painter.setBrush(QColor(SURFACE))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(event)


def open_startup_splash(app: QApplication) -> StartupSplash:
    """显示加载卡片并**立即泵一轮事件**——同步初始化开始前它就得画在屏幕上。"""
    splash = StartupSplash()
    splash.place_center()
    splash.show()
    app.processEvents()
    return splash


def close_startup_splash(splash: StartupSplash | None) -> None:
    """撤掉加载卡片（幂等，调用方不必先判空）。

    只 ``close()`` 不 ``deleteLater()``：卡片是短命小窗、生命周期由 ``startup_splash()``
    的 ``with`` 块兜住，Python 引用一释放即可回收——``deleteLater`` 需要事件循环跑完
    ``DeferredDelete`` 才生效，反而让「关掉之后再断言它不可见」变得不可靠。
    """
    if splash is None:
        return
    splash.close()


@contextmanager
def startup_splash(app: QApplication) -> Iterator[StartupSplash]:
    """``with startup_splash(app):`` 包住初始化；异常路径同样撤掉卡片。

    用法见 ``run_gui``：把 ``load_catalog`` → 窗口构造 → ``show()`` 整段放进 ``with``，
    撤卡片的那一刻主窗口已经在屏幕上，用户看到的是「加载提示 → 主界面」而不是白等。
    """
    splash = open_startup_splash(app)
    try:
        yield splash
    finally:
        close_startup_splash(splash)
