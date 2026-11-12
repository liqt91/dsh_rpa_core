"""可复用的元素捕获功能包（引擎 + 浮窗 + 编排器，无 Qt、无宿主壳）。

三层的分工：

- ``engine``：``HybridCaptureSession`` 工厂 + 手势文案 + 网页/桌面双腿真实状态；
- ``float_window``：win32 原生置顶浮窗（手势 + 双腿状态 + 取消按钮 + 躲鼠标）；
- ``controller``：一次捕获的状态机（``start / poll / cancel``），把上面两层串起来。

宿主只需两件事：
1. 捕获前后**让开目标** —— Qt：``showMinimized()/showNormal()``；ppx：前端
   ``ppx.call('window.minimize')/('window.restore')``；
2. 有效描述符产出后落盘/绑定 —— 通过 ``CaptureController(on_captured=...)`` 注入。

用法（单次捕获）::

    from rpa_core.capture_kit import CaptureController

    ctrl = CaptureController(on_captured=lambda d: save_element("userField", d))
    assert ctrl.start()["ok"]
    while (state := ctrl.poll())["state"] == "pending":
        time.sleep(0.2)
    if state["state"] == "ready":
        ...  # state["result"] 已是落盘后的描述符
"""

from .controller import CaptureController
from .engine import (
    NEVER_TIMEOUT,
    build_hybrid_session,
    capture_click_label,
    desktop_status,
    gesture_hint,
    status_snapshot,
    web_status,
)
from .float_window import CaptureFloat

__all__ = [
    "NEVER_TIMEOUT",
    "CaptureController",
    "CaptureFloat",
    "build_hybrid_session",
    "capture_click_label",
    "desktop_status",
    "gesture_hint",
    "status_snapshot",
    "web_status",
]
