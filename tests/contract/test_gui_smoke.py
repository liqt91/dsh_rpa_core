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
    _CONTROL_COMMANDS,
    CONTROL_GROUP_LABEL,
    ELSE_COMMAND_ID,
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
    # 最前面是固定的「流程控制」组，其后四个真实命名空间按固定顺序、用中文名成组
    expected = [CONTROL_GROUP_LABEL] + [
        NAMESPACE_LABELS[name] for name in NAMESPACE_ORDER
    ]
    actual = [tree.topLevelItem(i).text(0).split("（", 1)[0]
              for i in range(tree.topLevelItemCount())]
    assert actual == expected


def test_every_catalog_command_appears_once_as_leaf(window, catalog):
    tree = window.command_tree
    # 组数 = 4 个命名空间 + 1 个控制指令组
    assert tree.topLevelItemCount() == len(NAMESPACE_ORDER) + 1
    assert _leaf_count(tree) == len(catalog) + len(_CONTROL_COMMANDS)
    leaf_ids = {leaf.data(0, ROLE_COMMAND_ID) for leaf in _all_leaves(tree)}
    # 控制指令组里的所有条目（sequence/if/forEach/try/return/@else）都是流程控制，
    # 不属于 catalog；排除它们后叶子与 catalog 一一对应
    control_ids = {cid for cid, _, _ in _CONTROL_COMMANDS}
    assert leaf_ids - control_ids == set(catalog)


def test_group_label_carries_command_count(window, catalog):
    tree = window.command_tree
    # browser 组：标签形如「浏览器（30）」，计数与 catalog 实际一致
    browser_count = sum(1 for cid in catalog if cid.startswith("browser."))
    browser_group = next(
        tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0).startswith(NAMESPACE_LABELS["browser"])
    )
    assert browser_group.text(0) == f"浏览器（{browser_count}）"
    # 控制指令组同样带计数
    control_group = tree.topLevelItem(0)
    assert control_group.text(0) == f"{CONTROL_GROUP_LABEL}（{len(_CONTROL_COMMANDS)}）"


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
    assert _leaf_count(tree) == len(catalog) + len(_CONTROL_COMMANDS)
    assert all(
        not tree.topLevelItem(g).isHidden() for g in range(tree.topLevelItemCount())
    )


def test_filter_matches_control_command_by_chinese_label(window):
    """控制指令既能按中文显示名（否则）搜到，也能按命令 id（@else）搜到。"""
    tree = window.command_tree
    for keyword in ("否则", "else"):
        _apply_filter(tree, keyword)
        visible = {
            leaf.data(0, ROLE_COMMAND_ID)
            for leaf in _all_leaves(tree) if not leaf.isHidden()
        }
        assert ELSE_COMMAND_ID in visible
    _apply_filter(tree, "")


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
    """中栏画布默认加载内置示例流程；sequence root 已扁平化，
    顶层直接是原 sequence 的 children（至少 action / if / forEach / return）。"""
    from rpa_core.gui.flow_model import ROLE_NODE_TYPE

    model = window.flow_model
    assert model is not None
    root = model.invisibleRootItem()
    # 根容器扁平化后顶层直接是 action/forEach/if/return 等真实节点
    assert root.rowCount() >= 4  # open / loop / check / done
    first_child = root.child(0)
    assert first_child is not None
    assert first_child.data(ROLE_NODE_TYPE) in ("action", "forEach", "if", "try", "return")
