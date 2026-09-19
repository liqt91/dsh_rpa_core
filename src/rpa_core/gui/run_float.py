"""运行时悬浮窗（影刀式）：运行开始主窗口最小化，右下角置顶小窗实时显示执行步骤。

行为约定（与维护者确认）：
- 置顶小窗（320×130，右下角，可拖动），运行期间持续在场；
- 成功：2 秒后自动还原主窗口并关闭浮窗（由调用方 QTimer.singleShot 驱动）；
- 失败/取消：浮窗停留展示结果，点「还原」回主窗口；
- 数据来自运行中增量读取的 events.jsonl（stepStarted/stepCompleted/stepFailed）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

_WIDTH = 320
_HEIGHT = 130
_MARGIN = 16  # 距屏幕右下角的边距

_STATE_COLORS = {
    "running": "#0969da",
    "succeeded": "#1a7f37",
    "failed": "#cf222e",
    "cancelled": "#9a6700",
}


class RunFloatWindow(QWidget):
    """运行悬浮窗：状态点 + 当前步骤 + 进度 + 结果行 + 取消/还原按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        # macOS 专属语义（其它平台该属性为 no-op）：Qt 的 Qt.Tool 在 macOS 上
        # 对应 NSPanel，**应用一旦不激活，系统会隐藏全部 tool window**
        # （Qt 文档原话："By default, all tool windows are hidden when the
        # application is inactive"，源码侧即 hidesOnDeactivate=true）。
        # 本浮窗存在的意义恰恰是主窗口已最小化、用户正在别的 app 里看执行
        # 进度，所以必须显式打开这个开关；否则跑起来的瞬间它就跟主窗口一起
        # 消失，只剩 Dock 里的图标。
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFixedSize(_WIDTH, _HEIGHT)
        self._drag_pos: QPoint | None = None
        self.state = "running"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        header = QHBoxLayout()
        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet(f"color: {_STATE_COLORS['running']};")
        self.title_label = QLabel("运行中…")
        self.title_label.setStyleSheet("font-weight: 600;")
        header.addWidget(self.dot_label)
        header.addWidget(self.title_label, 1)
        self.progress_label = QLabel("0 步")
        header.addWidget(self.progress_label)
        layout.addLayout(header)

        self.step_label = QLabel("准备中…")
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label, 1)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.restore_button = QPushButton("还原")
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)

    # ---- 状态驱动 -----------------------------------------------------------
    def show_running(self, step_text: str, done_count: int) -> None:
        """运行中：当前步骤 + 已完成步数。"""
        self.state = "running"
        self.dot_label.setStyleSheet(f"color: {_STATE_COLORS['running']};")
        self.title_label.setText("运行中…")
        self.step_label.setText(step_text)
        self.step_label.setStyleSheet("")
        self.progress_label.setText(f"{done_count} 步")
        self.cancel_button.setEnabled(True)

    def show_result(self, status: str, detail: str = "") -> None:
        """终态：succeeded / failed / cancelled + 详情行。"""
        self.state = status
        color = _STATE_COLORS.get(status, "#57606a")
        self.dot_label.setStyleSheet(f"color: {color};")
        title = {
            "succeeded": "运行成功",
            "failed": "运行失败",
            "cancelled": "已取消",
        }.get(status, status)
        self.title_label.setText(title)
        self.step_label.setText(detail)
        if status in ("failed", "cancelled"):
            self.step_label.setStyleSheet(f"color: {color};")
        self.cancel_button.setEnabled(False)

    # ---- 定位与拖动 ---------------------------------------------------------
    def place_bottom_right(self) -> None:
        """贴到主屏幕可用区域右下角。"""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.right() - _WIDTH - _MARGIN,
            area.bottom() - _HEIGHT - _MARGIN,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆写
        # 无边框窗口自绘卡片底（白底圆角 + 边框），避免裸控件悬浮感
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
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)
