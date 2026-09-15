"""原生桌面 GUI 子系统（ADR 0014 方案 E，可选能力）。

本包依赖 PySide6 与 QDarkStyle，二者只在 ``gui`` optional extra 中声明，
默认 ``uv sync --all-groups`` 不会安装。因此：

- 本 ``__init__`` 不得在顶层 import PySide6，保证未装 extra 时
  ``import rpa_core.gui`` 本身不炸（CLI 也采用函数内延迟导入）；
- 真正的 Qt 入口在 :mod:`rpa_core.gui.app`，由 ``rpa-core gui`` 延迟加载；
- 未安装 extra 时 CLI 给出可操作的安装提示，而不是抛出原始 ImportError。
"""
