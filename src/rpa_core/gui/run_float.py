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
_HEIGHT = 150
_MARGIN = 16  # 距屏幕右下角的边距

_STATE_COLORS = {
    "running": "#0969da",
    "succeeded": "#1a7f37",
    "failed": "#cf222e",
    "cancelled": "#9a6700",
    "paused": "#bf8700",
    "recovery_required": "#8250df",
    "indeterminate": "#8250df",
}

# 不是「跑完」而是「等人工接手」的终态：浮窗要给出「继续」入口
_RESUMABLE_STATES = ("paused", "recovery_required", "indeterminate")


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
        # 已请求暂停但尚未落到边界：轮询会周期性调 show_running，这个标志让
        # 「已请求暂停…」的提示不被一秒数次的状态刷新冲掉。
        self._pause_pending = False

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
        self.pause_button = QPushButton("暂停")
        self.pause_button.setToolTip("当前步骤完成后停在节点边界（不打断已开始的命令）")
        self.continue_button = QPushButton("继续")
        self.continue_button.setToolTip("从暂停处继续；需人工确认的终态会先弹确认框")
        self.continue_button.setEnabled(False)
        self.step_button = QPushButton("单步")
        self.step_button.setToolTip(
            "单步：只执行一个节点，然后在下一个节点边界暂停（M24）"
        )
        self.step_button.setEnabled(False)
        self.restore_button = QPushButton("还原")
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.continue_button)
        buttons.addWidget(self.step_button)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)

    # ---- 状态驱动 -----------------------------------------------------------
    def clear_pause_pending(self) -> None:
        """新的一次运行开始：清掉上一次的「已请求暂停」残留。"""
        self._pause_pending = False

    def show_running(self, step_text: str, done_count: int) -> None:
        """运行中：当前步骤 + 已完成步数（已请求暂停时标题保持提示）。"""
        self.state = "running"
        self.dot_label.setStyleSheet(
            f"color: {_STATE_COLORS['paused' if self._pause_pending else 'running']};"
        )
        self.title_label.setText("已请求暂停…" if self._pause_pending else "运行中…")
        self.step_label.setText(step_text)
        self.step_label.setStyleSheet("")
        self.progress_label.setText(f"{done_count} 步")
        self.cancel_button.setEnabled(True)
        self.pause_button.setEnabled(not self._pause_pending)
        # 请求尚未落地时，「继续」= 撤销请求（run 还在跑）
        self.continue_button.setEnabled(self._pause_pending)
        self.step_button.setEnabled(False)  # 运行中不能单步

    def show_pausing(self) -> None:
        """已请求暂停、等待落到节点边界。"""
        self._pause_pending = True
        self.dot_label.setStyleSheet(f"color: {_STATE_COLORS['paused']};")
        self.title_label.setText("已请求暂停…")
        self.step_label.setText("当前步骤完成后停在节点边界")
        self.step_label.setStyleSheet("")
        self.pause_button.setEnabled(False)
        self.continue_button.setEnabled(True)

    def show_result(self, status: str, detail: str = "") -> None:
        """终态：succeeded / failed / cancelled / paused / 需确认恢复的两种 + 详情行。"""
        self.state = status
        self._pause_pending = False
        color = _STATE_COLORS.get(status, "#57606a")
        self.dot_label.setStyleSheet(f"color: {color};")
        title = {
            "succeeded": "运行成功",
            "failed": "运行失败",
            "cancelled": "已取消",
            "paused": "已暂停（可继续）",
            "recovery_required": "待确认后可恢复",
            "indeterminate": "结果不确定，待确认",
        }.get(status, status)
        self.title_label.setText(title)
        self.step_label.setText(detail)
        if status in ("failed", "cancelled", "paused", "recovery_required", "indeterminate"):
            self.step_label.setStyleSheet(f"color: {color};")
        self.cancel_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.set_continue_enabled(status in _RESUMABLE_STATES)

    def set_continue_enabled(self, enabled: bool) -> None:
        self.continue_button.setEnabled(enabled)
        # 单步只在「已暂停」态可用（需确认恢复的两种终态不能盲单步）
        self.step_button.setEnabled(enabled and self.state == "paused")

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
