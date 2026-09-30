"""契约测试共用夹具。

**QSettings 隔离（M49 P1-1）**：GUI 会把窗口几何 / 三栏比例 / 对话框尺寸写进 QSettings。
若测试直接读写本机配置，会出现两类假象——① 本机能过、CI 红；② 开发机上用过一次 GUI
之后套件就变红（M47.5b 已吃过「测试读了真实环境」的同类教训）。

产品代码不需要为测试留任何开关：``rpa_core.gui.persist.gui_settings()`` 显式使用
IniFormat/UserScope，所以这里把 IniFormat 的落盘目录改到 tmp_path 就完成了隔离。
"""

from __future__ import annotations

import pytest

try:
    from PySide6.QtCore import QSettings
except ImportError:  # pragma: no cover - 缺 GUI 依赖时整组 GUI 测试本就跳过
    QSettings = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def isolated_gui_settings(tmp_path):
    """把 GUI 设置的整体落盘位置搬到临时目录（含 QSettings 默认格式）。"""
    if QSettings is None:
        yield
        return
    settings_dir = tmp_path / "qsettings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    yield
