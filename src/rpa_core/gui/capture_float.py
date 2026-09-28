"""捕获悬浮窗（M40）：捕获期间右下角置顶，显示通道状态 + 操作手势 + 取消入口。

为什么需要它：捕获开始时主窗 `showMinimized()`（不遮挡目标窗口），而**状态栏是捕获
唯一的反馈面**——窗口一最小化，用户就再也看不到「捕获中 / 失败 / 该怎么操作」。维护者
2026-09-28 报障的三句里就有这条形状：点了捕获什么都没发生，再点一次只得到「已有捕获
会话进行中」，而 Esc 是**腿内部**的手势，一条腿没在听时用户无路可走。

行为约定：

- 置顶小窗（默认右下角、可拖动），捕获期间持续在场；结束由调用方关闭；
  鼠标靠近默认位置时自动翻到左上角躲开（见 ``capture_float_origin``）；
- 两条腿的**真实状态**各自一行（网页通道 / 桌面通道），不合并成一句——用户要据此
  决定「该去网页里点还是去桌面应用上按 F9」；
- 「取消捕获」按钮是捕获期唯一的确定性出口（不依赖任何一条腿响应键盘）；
- **不显示倒计时**（M41 S4，维护者 2026-09-28）：「90 秒」是**兜底**（两条腿都卡死时
  的唯一出路，见 `app.CAPTURE_TIMEOUT_SECONDS`），不是用户要管的时限——正常捕获几秒
  就结束，这个数字从头到尾读不完，却把「等待上限」表达成「限时任务」（而超时**没有
  任何惩罚**，重点一次即可）。用户需要的是「去哪操作」（两条腿的真实状态）与「怎么
  退出」；上限本身留在后台。

观感与 `run_float.py` 保持一致（无边框 + 自绘卡片底），两者是同一类「主窗收起时的
在场小窗」。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

_WIDTH = 380
_HEIGHT = 158
_MARGIN = 16  # 距屏幕右下角的边距

_ONLINE = "#1a7f37"
_OFFLINE = "#cf222e"
_NEUTRAL = "#57606a"

# 鼠标预判避让（M41 S3）：浮窗是**真实窗口**（无 WS_EX_TRANSPARENT，不吃穿透），
# 压在鼠标路径上时同时挡住视觉与点击——用户会「点不中」它下方的元素。维护者
# 2026-09-28 报：「捕捉元素的桌面悬浮框，在鼠标即将移动到悬浮框的时候，悬浮框
# 移动到屏幕另一侧，避免挡住实际需要捕获的元素」。
# 判定基准恒为**默认位置**（右下角）那一个矩形，与浮窗当前所在处无关 → 无状态、
# 不抖动：鼠标在右下角区域时给左上角，离开后自动回右下角。
AVOID_PAD = 80


def capture_float_origin(
    area: tuple[int, int, int, int],
    size: tuple[int, int],
    cursor: tuple[int, int] | None,
    *,
    margin: int = _MARGIN,
    avoid_pad: int = AVOID_PAD,
) -> tuple[int, int]:
    """浮窗左上角坐标：默认贴右下角，鼠标靠近时翻到对侧（左上角）。

    - ``area``：屏幕可用区域 ``(x, y, w, h)``
    - ``size``：浮窗尺寸 ``(w, h)``
    - ``cursor``：鼠标屏幕坐标 ``(x, y)``；``None`` 表示不做避让

    鼠标落入「默认位置矩形外扩 ``avoid_pad``」即翻到左上角，否则回右下角。
    返回的是坐标而非是否翻转——调用方据此 ``move``，纯函数好测。
    """
    ax, ay, aw, ah = area
    ww, wh = size
    default_x = ax + aw - ww - margin
    default_y = ay + ah - wh - margin
    if cursor is None:
        return default_x, default_y
    cx, cy = cursor
    near_default = (
        default_x - avoid_pad <= cx <= default_x + ww + avoid_pad
        and default_y - avoid_pad <= cy <= default_y + wh + avoid_pad
    )
    if near_default:
        return ax + margin, ay + margin
    return default_x, default_y


class CaptureFloatWindow(QWidget):
    """捕获悬浮窗：标题 + 手势提示 + 两腿状态 + 取消按钮。"""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        # macOS 语义（其它平台 no-op）：Qt.Tool 在 macOS 上是 NSPanel，应用一失活
        # 系统就隐藏全部 tool window——而本窗存在的意义正是主窗已最小化、用户在
        # 别的应用里操作时仍能看到状态（同 run_float）。
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFixedSize(_WIDTH, _HEIGHT)
        self._drag_pos: QPoint | None = None
        self.cancelling = False
        # 用户手动拖动过 → 尊重其选择，不再自动避让（见 avoid_cursor）
        self.user_positioned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)

        header = QHBoxLayout()
        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet(f"color: {_NEUTRAL};")
        self.title_label = QLabel("正在捕获元素…")
        self.title_label.setStyleSheet("font-weight: 600;")
        header.addWidget(self.dot_label)
        header.addWidget(self.title_label, 1)
        layout.addLayout(header)

        self.gesture_label = QLabel("")
        self.gesture_label.setWordWrap(True)
        layout.addWidget(self.gesture_label)

        # 两条腿各一行：用户据此决定去哪边操作，合并成一句会掩盖「一边已失效」
        self.web_label = QLabel("")
        self.web_label.setWordWrap(True)
        self.desktop_label = QLabel("")
        self.desktop_label.setWordWrap(True)
        layout.addWidget(self.web_label)
        layout.addWidget(self.desktop_label)

        buttons = QHBoxLayout()
        self.hint_label = QLabel("Esc 取消")
        self.hint_label.setStyleSheet(f"color: {_NEUTRAL};")
        buttons.addWidget(self.hint_label)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消捕获")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    # ---- 状态驱动 -----------------------------------------------------------
    def show_capture(
        self,
        *,
        gesture: str,
        web: str,
        desktop: str,
        web_ok: bool = True,
        desktop_ok: bool = True,
    ) -> None:
        """填入本次捕获的实况（手势 / 两腿状态）并置顶显示。"""
        self.cancelling = False
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("取消捕获")
        self.dot_label.setStyleSheet(f"color: {_NEUTRAL};")
        self.title_label.setText("正在捕获元素…")
        self.gesture_label.setText(gesture)
        self.set_channels(web=web, desktop=desktop, web_ok=web_ok, desktop_ok=desktop_ok)

    def set_channels(
        self, *, web: str, desktop: str, web_ok: bool = True, desktop_ok: bool = True
    ) -> None:
        """更新两条腿的状态行（运行期可再调：如扩展腿被判死后改文案）。"""
        self.web_label.setText(web)
        self.web_label.setStyleSheet(f"color: {_ONLINE if web_ok else _OFFLINE};")
        self.desktop_label.setText(desktop)
        self.desktop_label.setStyleSheet(
            f"color: {_ONLINE if desktop_ok else _OFFLINE};"
        )

    def mark_cancelling(self) -> None:
        """已请求取消（等会话真正收场，浮窗由调用方关闭）。"""
        self.cancelling = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在取消…")
        self.title_label.setText("正在取消捕获…")

    def _on_cancel_clicked(self) -> None:
        if self.cancelling:
            return
        self.mark_cancelling()
        self.cancel_requested.emit()

    # ---- 定位与拖动 ---------------------------------------------------------
    def avoid_cursor(self, cursor: QPoint | None) -> None:
        """按鼠标位置定位：默认贴右下角，鼠标靠近时翻到对侧（左上角）。

        ``cursor=None`` 表示不避让（直接贴右下角）；用户手动拖过则本方法不再动作。
        由调用方按节拍驱动（``app.py`` 的避让定时器）——鼠标「即将移动到浮窗」
        时浮窗已在另一侧，而不是等压上来才让开。
        """
        if self.user_positioned:
            return
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        point = (cursor.x(), cursor.y()) if cursor is not None else None
        x, y = capture_float_origin(
            (area.x(), area.y(), area.width(), area.height()),
            (_WIDTH, _HEIGHT),
            point,
        )
        if self.x() != x or self.y() != y:
            self.move(x, y)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆写
        # 无边框窗口自绘卡片底（白底圆角 + 边框），与 run_float 一致
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#c0c4c8")))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.user_positioned = True  # 用户接管位置：停止自动避让
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)
