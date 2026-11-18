"""可复用的元素捕获功能包（引擎 + 浮窗 + 编排器，无 Qt、无宿主壳）。

三层的分工：

- ``engine``：``HybridCaptureSession`` 工厂 + 手势文案 + 网页/桌面双腿真实状态；
- ``float_window``：win32 原生置顶浮窗（手势 + 双腿状态 + 取消按钮 + 躲鼠标）；
- ``controller``：一次捕获的状态机（``start / poll / cancel``），把上面两层串起来；
- ``element_editing``：无 UI 的编辑语义层（名字/定位校验、编辑模型、层级重组），
  供工作台 HTML 编辑框与 win32 框共用，宿主只负责会话与落盘。
- ``desktop_editing``：桌面 locator ↔ 影刀式层级/属性模型（重组回完整 selector）。

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
from .desktop_editing import compose_selector, nodes_from_descriptor
from .edit_dialog import ElementEditDialog
from .element_editing import (
    apply_selector,
    build_edit_model,
    compose,
    edit_text_for,
    is_valid_name,
    levels_from_path,
    validate_name,
    validate_selector,
)
from .engine import (
    NEVER_TIMEOUT,
    build_hybrid_session,
    capture_click_label,
    desktop_status,
    ensure_extension_host,
    extension_dir,
    extension_installed,
    gesture_hint,
    status_snapshot,
    web_status,
)
from .float_window import CaptureFloat

__all__ = [
    "NEVER_TIMEOUT",
    "CaptureController",
    "CaptureFloat",
    "ElementEditDialog",
    "apply_selector",
    "build_edit_model",
    "build_hybrid_session",
    "capture_click_label",
    "compose",
    "compose_selector",
    "desktop_status",
    "edit_text_for",
    "ensure_extension_host",
    "extension_dir",
    "extension_installed",
    "gesture_hint",
    "is_valid_name",
    "levels_from_path",
    "nodes_from_descriptor",
    "status_snapshot",
    "validate_name",
    "validate_selector",
    "web_status",
]
