"""原生桌面 GUI（ADR 0014 方案 E）第一个切片的 headless 冒烟测试。

PySide6 属于可选 ``gui`` extra：未安装时整模块跳过，保证默认门禁
（``uv sync --all-groups`` 无 Qt）保持全绿；安装后用 ``offscreen`` 平台
插件在无显示环境下构建真实主窗口，验证指令树确实加载了真实 catalog。
"""

import os

# 必须在导入 QtWidgets / 创建 QApplication 之前指定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.gui.app import (  # noqa: E402
    NAMESPACE_LABELS,
    NAMESPACE_ORDER,
    ROLE_COMMAND_ID,
    MainWindow,
    _apply_filter,
    build_application,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(_commands_root())


@pytest.fixture(scope="module")
def qapp():
    # 进程内 QApplication 单例：build_application 已做 instance 复用。
    return build_application()


@pytest.fixture()
def window(qapp, catalog):
    return MainWindow(catalog)


def _leaf_count(tree) -> int:
    return sum(
        tree.topLevelItem(group).childCount() for group in range(tree.topLevelItemCount())
    )


def _all_leaves(tree):
    for group in range(tree.topLevelItemCount()):
        group_item = tree.topLevelItem(group)
        for child in range(group_item.childCount()):
            yield group_item.child(child)


def test_tree_groups_follow_namespace_order(window, catalog):
    tree = window.command_tree
    # 四个真实命名空间按固定顺序、用中文名成组
    expected = [NAMESPACE_LABELS[name] for name in NAMESPACE_ORDER]
    actual = [tree.topLevelItem(i).text(0).split("（", 1)[0]
              for i in range(tree.topLevelItemCount())]
    assert actual == expected


def test_every_catalog_command_appears_once_as_leaf(window, catalog):
    tree = window.command_tree
    assert tree.topLevelItemCount() == len(NAMESPACE_ORDER)
    assert _leaf_count(tree) == len(catalog)
    leaf_ids = {leaf.data(0, ROLE_COMMAND_ID) for leaf in _all_leaves(tree)}
    assert leaf_ids == set(catalog)


def test_group_label_carries_command_count(window, catalog):
    tree = window.command_tree
    # 第一组 browser：标签形如「浏览器（30）」，计数与 catalog 实际一致
    browser_count = sum(1 for cid in catalog if cid.startswith("browser."))
    assert tree.topLevelItem(0).text(0) == f"浏览器（{browser_count}）"


def test_filter_only_keeps_matching_leaves(window):
    tree = window.command_tree
    _apply_filter(tree, "navigate")
    visible = [
        leaf.data(0, ROLE_COMMAND_ID)
        for leaf in _all_leaves(tree)
        if not leaf.isHidden()
    ]
    assert visible  # 至少命中 browser.navigate
    assert all("navigate" in cid for cid in visible)
    # 无命中的分组整体隐藏
    for group in range(tree.topLevelItemCount()):
        group_item = tree.topLevelItem(group)
        has_visible_leaf = any(
            not group_item.child(i).isHidden() for i in range(group_item.childCount())
        )
        assert group_item.isHidden() != has_visible_leaf


def test_clear_filter_restores_all_groups(window, catalog):
    tree = window.command_tree
    _apply_filter(tree, "navigate")
    _apply_filter(tree, "")
    assert _leaf_count(tree) == len(catalog)
    assert all(
        not tree.topLevelItem(g).isHidden() for g in range(tree.topLevelItemCount())
    )


def test_command_tree_selection_does_not_raise(window):
    """选中左树命令叶子：选择链路可用（参数表单在后续切片接入）。"""
    tree = window.command_tree
    target = next(
        leaf for leaf in _all_leaves(tree)
        if leaf.data(0, ROLE_COMMAND_ID) == "browser.navigate"
    )
    tree.setCurrentItem(target)
    assert tree.currentItem().data(0, ROLE_COMMAND_ID) == "browser.navigate"


def test_window_has_canvas_with_sample_flow(window):
    """中栏画布默认加载内置示例流程，根节点为 sequence。"""
    from rpa_core.gui.flow_model import ROLE_NODE_TYPE

    model = window.flow_model
    assert model is not None
    root = model.item(0)
    assert root.data(ROLE_NODE_TYPE) == "sequence"
    assert root.rowCount() >= 3  # action / if / forEach / return
