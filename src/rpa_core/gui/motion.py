"""界面过渡动画（M49 P2）。

只做**关键处**的一点点过渡，不做全应用动画：界面切换（工作台 ↔ 编辑器）与面板首次
出现时给 150–200ms 的淡入，避免生硬的瞬切。三条纪律：

1. **可用环境变量关掉**：``RPA_GUI_ANIMATIONS=0`` 时全部退化为「直接显示」。
   这是排障与自动化测试的开关（动画会让「什么时候才轮到我的输入」变得不可预测）。
2. **失败安全**：禁用或任何异常路径都必须把窗口/控件置于**完全可见**状态——
   宁可没有动画，也不能让界面停在半透明。
3. **不留残留**：控件淡入用的 QGraphicsOpacityEffect 在动画结束后立刻摘掉，
   否则后续任何 setGraphicsEffect 调用都会与它打架（且半透明状态会被固化）。

实现选择：顶层窗口用 ``windowOpacity``（不需要图形特效，跨平台一致）；面板用
``QGraphicsOpacityEffect``（窗口级不透明度改不了单个控件）。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

DEFAULT_DURATION_MS = 160


def animations_enabled() -> bool:
    """动画是否启用（``RPA_GUI_ANIMATIONS=0`` 关闭；其余取值均视为启用）。"""
    return os.environ.get("RPA_GUI_ANIMATIONS", "1") != "0"


def fade_window(
    window: QWidget, *, duration: int = DEFAULT_DURATION_MS, start: float = 0.0
) -> QPropertyAnimation | None:
    """顶层窗口淡入；禁用时直接置为完全不透明并返回 None。"""
    try:
        if not animations_enabled():
            window.setWindowOpacity(1.0)
            return None
        animation = QPropertyAnimation(window, b"windowOpacity", window)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(1.0)
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return animation
    except Exception:  # noqa: BLE001 - 动画失败绝不能把窗口留在半透明
        window.setWindowOpacity(1.0)
        return None


def fade_widget(
    widget: QWidget, *, duration: int = DEFAULT_DURATION_MS, start: float = 0.0
) -> QPropertyAnimation | None:
    """控件淡入（面板首次出现用）；动画结束即摘掉特效，不留残留。"""
    try:
        if not animations_enabled():
            widget.setGraphicsEffect(None)
            return None
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(start)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(1.0)

        def _cleanup() -> None:
            # 只有当前特效还是我们装的那个才摘（避免摘掉别人后装的）
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)

        animation.finished.connect(_cleanup)
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return animation
    except Exception:  # noqa: BLE001 - 同上：宁可没动画，也不要半透明
        widget.setGraphicsEffect(None)
        return None
