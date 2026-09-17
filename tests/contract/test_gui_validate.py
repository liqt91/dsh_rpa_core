"""编译校验契约测试（GUI 功能补齐 切 D）。

- collect_validation_issues：schema / 必填参数缺口 / 编译器静态检查三层；
- MainWindow._validate_workflow：通过与否的状态栏反馈、问题节点定位。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from rpa_core.gui.flow_model import ROLE_ARGS_RAW, ROLE_NODE_ID  # noqa: E402


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
def window(catalog):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog)


def test_sample_workflow_is_clean(catalog):
    """内置示例流程必须零问题（默认视图不应自带错误标记）。"""
    from rpa_core.gui.app import SAMPLE_WORKFLOW, collect_validation_issues

    assert collect_validation_issues(SAMPLE_WORKFLOW, catalog) == []


def test_missing_required_param_reports_node_id(catalog):
    from rpa_core.gui.app import SAMPLE_WORKFLOW, collect_validation_issues

    document = copy.deepcopy(SAMPLE_WORKFLOW)
    # browser.navigate 的 browserType 是必填参数
    open_node = document["root"]["children"][0]
    del open_node["with"]["browserType"]
    issues = collect_validation_issues(document, catalog)
    assert any(
        "browserType" in message and node_id == "open" for message, node_id in issues
    )


def test_unknown_command_reports_node_id(catalog):
    from rpa_core.gui.app import SAMPLE_WORKFLOW, collect_validation_issues

    document = copy.deepcopy(SAMPLE_WORKFLOW)
    document["root"]["children"][0]["command"] = "browser.notExist"
    issues = collect_validation_issues(document, catalog)
    assert any("未知指令" in message and node_id == "open" for message, node_id in issues)


def test_unsafe_retry_is_compiler_issue(catalog):
    from rpa_core.gui.app import SAMPLE_WORKFLOW, collect_validation_issues

    document = copy.deepcopy(SAMPLE_WORKFLOW)
    # browser.navigate 的 effect.replay=unsafe，编译器拒绝配重试
    document["root"]["children"][0]["retry_count"] = 3
    issues = collect_validation_issues(document, catalog)
    assert any("编译失败" in message for message, _ in issues)


def test_schema_error_short_circuits(catalog):
    from rpa_core.gui.app import collect_validation_issues

    issues = collect_validation_issues({"root": {"type": "bogus"}}, catalog)
    assert len(issues) == 1
    assert "schema 校验失败" in issues[0][0]


def test_validate_workflow_statusbar_feedback(window):
    assert window._validate_workflow(show_dialog=False) is True
    assert "校验通过" in window.statusBar().currentMessage()


def test_validate_workflow_locates_offending_node(window):
    # 删掉 read 节点的必填 selector，校验应失败并把画布选中定位到 read
    model = window.flow_model
    read_item = model.find_by_id("read")
    read_item.data(ROLE_ARGS_RAW).raw["with"].pop("selector")
    read_item.data(ROLE_ARGS_RAW).args.pop("selector")

    assert window._validate_workflow(show_dialog=False) is False
    current = window.canvas_view.currentIndex()
    assert current.data(ROLE_NODE_ID) == "read"
