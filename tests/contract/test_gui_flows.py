"""流程库管理契约测试（GUI 功能补齐 切 F）。

- 命名流程保存进 workflows 目录（WorkflowDirStore 同 Web 存储）；
- 流程库下拉列表刷新与打开闭环；
- 非法流程名拒绝；CLI gui --workflows 透传。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


@pytest.fixture()
def window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog, workflows_root=tmp_path / "workflows")


def test_store_combo_lists_saved_flows(window):
    assert window._store is not None
    assert window.flow_combo is not None
    window._save_named_flow("alpha")
    items = [
        window.flow_combo.itemData(i) for i in range(window.flow_combo.count())
    ]
    assert "alpha" in items
    # 落盘位置与 Web 一致：<root>/<name>/workflow.json
    saved = window._store.root / "alpha" / "workflow.json"
    assert saved.is_file()
    document = json.loads(saved.read_text(encoding="utf-8"))
    assert document["root"]["type"] == "sequence"
    assert window.flow_path == saved
    assert not window._dirty


def test_save_named_flow_rejects_invalid_name(window):
    assert window._save_named_flow("bad/../escape") is None
    assert "保存失败" in window.statusBar().currentMessage()


def test_open_named_flow_roundtrip(window):
    window._save_named_flow("beta")
    # 弄脏当前画布再打开，应提示丢弃——offscreen 下直接清脏绕过弹窗
    window._set_dirty(False)
    window._open_named_flow("beta")
    assert window._current_flow_name() == "beta"
    assert window.flow_combo.currentData() == "beta"


def test_window_without_store_has_no_combo(catalog):
    from rpa_core.gui.app import MainWindow

    plain = MainWindow(catalog)
    assert plain._store is None
    assert plain.flow_combo is None


def test_current_flow_name_only_inside_store(window, tmp_path):
    assert window._current_flow_name() is None  # 示例流程不在库中
    window._save_named_flow("gamma")
    assert window._current_flow_name() == "gamma"


def test_cli_gui_forwards_workflows_root(catalog, tmp_path):
    from rpa_core.gui.app import build_main_window

    win = build_main_window(catalog, workflows_root=tmp_path / "wf")
    assert win._store is not None
    assert win._store.root == (tmp_path / "wf")
