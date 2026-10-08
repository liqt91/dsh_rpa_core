"""M48 桌面契约一片：D1 祖先链 path + D2 控件级 matchMode（模型与两执行器消费面）。

背景：`docs/yingdao-gap-catchup.md` §4「M48 桌面契约一片」。本文件锁三件事：

1. **D1 模型**：`DesktopLocator.path` 是新可选字段（`LocatorStep` 每级至少一键）；
   **不出现时行为零变化**（向后兼容是硬要求，旧元素文档零迁移）。
2. **D1 agent 侧**：祖先链**不含目标本身**，且转换取不到键的级要跳过（不发明空 step）。
3. **D1/D2 执行器侧**：`_find` 无 path 时与今天**逐字节同行为**；有 path 时先收窄；
   `matchMode` 扩到控件级 `automationId`/`name` 后，**未显式给 matchMode 仍是 exact**。

真机边界：桌面 E2E 会抢前台，AGENTS 定「按需启用」。本文件全部走替身元素，
**不冒充真机结论**（真实 UIA 树上的 path 生成仍待真机复验）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from rpa_core.model.desktop import DesktopLocator, LocatorStep


# --------------------------------------------------------------------------
# 替身 UI 元素（agent 侧与执行器侧共用）
# --------------------------------------------------------------------------
class FakeInfo:
    """UIA ElementInfo 替身：只需 path 生成/匹配要读的四个键。"""

    def __init__(self, *, control_type=None, automation_id=None, name=None,
                 class_name=None):
        self.control_type = control_type
        self.automation_id = automation_id
        self.name = name
        self.class_name = class_name


class FakeElement:
    """UIA 元素替身：`element_info` + `parent` + `handle`。

    **同时转发 info 的四个键**（control_type / automation_id / name / class_name）：
    真机上祖先链里的元素就是 `UIAElementInfo`（info 层级对象，见
    `desktop_agent._element_from_point` / `_dfs_smallest_at`），`_locator_step_for`
    直接读这四个键。替身若只挂 `element_info` 而不转发，`_locator_step_for` 恒返 None
    ⇒ `path` 恒为空却**不报错**（2026-10-08 抓到：判据形状与真机不符导致的假绿/假红）。
    """

    def __init__(self, info: FakeInfo, *, handle: int = 0, parent=None):
        self.element_info = info
        self.handle = handle
        self.parent = parent
        self.children_result: list[FakeElement] = []

    # --- info 四键转发（与真机 UIAElementInfo 的鸭子类型对齐）---
    @property
    def control_type(self):
        return self.element_info.control_type

    @property
    def automation_id(self):
        return self.element_info.automation_id

    @property
    def name(self):
        return self.element_info.name

    @property
    def class_name(self):
        return self.element_info.class_name

    def children(self):
        return list(self.children_result)

    def descendants(self, **criteria):
        """按 criteria 过滤全子树（扁平后代的近似：这里用 children 递归）。"""
        out: list[FakeElement] = []

        def walk(node: FakeElement):
            for child in node.children_result:
                out.append(child)
                walk(child)

        walk(self)
        for key, value in criteria.items():
            if key == "control_type":
                out = [e for e in out if e.element_info.control_type == value]
            elif key == "title":
                out = [e for e in out if e.element_info.name == value]
            elif key == "class_name":
                out = [e for e in out if e.element_info.class_name == value]
        return out


# --------------------------------------------------------------------------
# D1 模型层
# --------------------------------------------------------------------------
def test_locator_accepts_optional_path_with_steps():
    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "path": [
                {"controlType": "Window", "className": "Notepad"},
                {"name": "panel"},
            ],
        }
    )
    assert locator.path is not None
    assert len(locator.path) == 2
    assert locator.path[0].class_name == "Notepad"
    assert locator.path[1].name == "panel"


def test_locator_without_path_keeps_old_behavior():
    """旧文档零迁移：不给 path 时该字段为 None，其余字段逐一不变。"""
    locator = DesktopLocator.model_validate(
        {"backend": "uia", "controlType": "Button", "automationId": "ok"}
    )
    assert locator.path is None
    assert locator.automation_id == "ok"
    assert locator.control_type == "Button"


def test_empty_path_is_rejected():
    with pytest.raises(ValidationError):
        DesktopLocator.model_validate(
            {"backend": "uia", "automationId": "x", "path": []}
        )


def test_empty_step_is_rejected():
    """每级至少一个键——空 step 会在运行期成为「恒匹配」的静默过滤器。"""
    with pytest.raises(ValidationError):
        LocatorStep.model_validate({})


def test_unknown_step_key_is_rejected():
    with pytest.raises(ValidationError):
        LocatorStep.model_validate({"bogusKey": "x"})


# --------------------------------------------------------------------------
# D2 模型层
# --------------------------------------------------------------------------
def test_match_mode_defaults_to_none_meaning_exact():
    """未给 matchMode ⇒ 字段为 None；执行器按 exact 处理（行为零变化）。"""
    locator = DesktopLocator.model_validate({"backend": "uia", "automationId": "x"})
    assert locator.match_mode is None


def test_match_mode_accepts_known_values():
    for mode in ("exact", "contains", "regex"):
        locator = DesktopLocator.model_validate(
            {"backend": "uia", "automationId": "x", "matchMode": mode}
        )
        assert locator.match_mode == mode


def test_match_mode_rejects_unknown_value():
    with pytest.raises(ValidationError):
        DesktopLocator.model_validate(
            {"backend": "uia", "automationId": "x", "matchMode": "fuzzy"}
        )


# --------------------------------------------------------------------------
# D1 agent 侧：LocatorStep 生成 + 祖先链（不含目标本身）
# --------------------------------------------------------------------------
def test_locator_step_for_reads_four_keys():
    from rpa_core.capture.desktop_agent import _locator_step_for

    step = _locator_step_for(
        FakeInfo(control_type="Button", automation_id="ok", name="提交",
                 class_name="Button")
    )
    assert step == {
        "controlType": "Button",
        "automationId": "ok",
        "name": "提交",
        "className": "Button",
    }


def test_locator_step_for_returns_none_when_all_keys_absent():
    """一个键都取不到 ⇒ None（调用方跳过，不发明空 step）。"""
    from rpa_core.capture.desktop_agent import _locator_step_for

    assert _locator_step_for(FakeInfo()) is None


def test_dfs_path_excludes_target_itself():
    """`_dfs_smallest_at(path_out=…)` 的链里**不含最终命中的目标**。"""
    from rpa_core.capture.desktop_agent import _dfs_smallest_at

    root = FakeElement(FakeInfo(control_type="Window"), handle=10)
    panel = FakeElement(FakeInfo(control_type="Pane", automation_id="p"), handle=11,
                        parent=root)
    target = FakeElement(FakeInfo(control_type="Button", automation_id="t"), handle=12,
                         parent=panel)
    root.children_result = [panel]
    panel.children_result = [target]
    # 矩形包含关系：全部覆盖点
    for el in (root, panel, target):
        el.rectangle = _Rect(0, 0, 100, 100)

    path: list = []
    leaf = _dfs_smallest_at(root, 5, 5, path_out=path)
    assert leaf is target
    assert path == [root, panel]  # 起点算一级；不含 target


def test_ancestor_chain_stops_at_root_and_reverses():
    """屏幕级路径的父链上溯：根在前、不含 info 自身、到 root 停。"""
    from rpa_core.capture.desktop_agent import _ancestor_chain

    root = FakeElement(FakeInfo(control_type="Window"), handle=10)
    mid = FakeElement(FakeInfo(control_type="Pane"), handle=11, parent=root)
    leaf = FakeElement(FakeInfo(control_type="Button"), handle=12, parent=mid)

    chain = _ancestor_chain(leaf, 10)
    assert chain == [root, mid]
    assert leaf not in chain


def test_ancestor_chain_guards_against_cycles():
    """防死循环：深度上限 + handle 重复即停（虚拟元素 handle=0 不参与判定）。"""
    from rpa_core.capture.desktop_agent import _ancestor_chain

    a = FakeElement(FakeInfo(control_type="Pane"), handle=20)
    b = FakeElement(FakeInfo(control_type="Pane"), handle=21, parent=a)
    a.parent = b  # 环
    leaf = FakeElement(FakeInfo(control_type="Button"), handle=22, parent=b)

    chain = _ancestor_chain(leaf, 0)  # root=0 ⇒ 不靠 root 停
    assert len(chain) <= 32


# --------------------------------------------------------------------------
# D1 执行器侧：path 收窄（uia）
# --------------------------------------------------------------------------
def _uia_executor():
    from rpa_core.executors.desktop import DesktopExecutor

    return DesktopExecutor


def test_step_matches_requires_all_given_keys():
    cls = _uia_executor()
    step = MagicMock()
    step.control_type = "Pane"
    step.automation_id = "p"
    step.name = None
    step.class_name = None

    hit = FakeElement(FakeInfo(control_type="Pane", automation_id="p"))
    miss_aid = FakeElement(FakeInfo(control_type="Pane", automation_id="other"))
    assert cls._step_matches(step, hit) is True
    assert cls._step_matches(step, miss_aid) is False


def test_find_without_path_scans_window_directly():
    """无 path ⇒ 与今天完全同路径（直接在 window 上找，不经过收窄）。"""
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    target = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    window.children_result = [target]

    locator = DesktopLocator.model_validate(
        {"backend": "uia", "controlType": "Edit", "automationId": "q"}
    )
    assert cls._find(window, locator) == [target]


def test_find_with_path_narrows_before_target():
    """有 path ⇒ 只在收窄出的容器里找目标；容器外同名元素不得命中。"""
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    good_panel = FakeElement(FakeInfo(control_type="Pane", automation_id="panel-a"))
    bad_panel = FakeElement(FakeInfo(control_type="Pane", automation_id="panel-b"))
    inside = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    outside = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    good_panel.children_result = [inside]
    bad_panel.children_result = [outside]
    window.children_result = [good_panel, bad_panel]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "q",
            "path": [{"controlType": "Pane", "automationId": "panel-a"}],
        }
    )
    assert cls._find(window, locator) == [inside]


def test_find_with_unmatched_path_returns_empty():
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    target = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    window.children_result = [target]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "q",
            "path": [{"controlType": "Pane", "automationId": "nope"}],
        }
    )
    assert cls._find(window, locator) == []


# --------------------------------------------------------------------------
# D1 真机回归钉子（2026-10-08 真机复验抓到）
#
# 上面 `test_find_with_path_narrows_before_target` 用的是「path 首级是 window 的**后代**」
# 这个替身形状——而**真机生成的 path 首级曾是 window 自身**（祖先链里根窗口算了一级）。
# UIA 里窗口不属于自己的后代，于是「只看后代」的收窄**每一条真实 path 都在第一级归零、
# `_find` 恒返回 0 命中**——而上面那条替身用例照样绿。
# 这正是「替身只能证明算法在给定形状上对，证明不了算法面对的**真实形状**是什么」。
#
# 定案（选「产侧修」而不是「放宽谓词」）：捕获侧不再产出根窗口那一级
# （`desktop_agent._path_steps_from`），执行器 `_narrow_by_path` 保持「只看后代」。
# 理由：根级不携带任何收窄信息，放宽谓词会让它对别的用法也变松。
# --------------------------------------------------------------------------
def test_ancestor_chain_still_includes_root_but_path_steps_drop_it():
    """分两层：`_ancestor_chain` 仍回传根（它要用来判终止），但转 path 时剥掉。"""
    from rpa_core.capture.desktop_agent import _ancestor_chain, _path_steps_from

    root = FakeElement(FakeInfo(control_type="Window", automation_id="mainWindow"), handle=10)
    mid = FakeElement(FakeInfo(control_type="Pane", automation_id="p"), handle=11, parent=root)
    leaf = FakeElement(FakeInfo(control_type="Button", automation_id="b"), handle=12, parent=mid)

    chain = _ancestor_chain(leaf, 10)
    assert chain == [root, mid]  # 链本身含根
    steps = _path_steps_from(chain, 10)
    assert steps == [{"controlType": "Pane", "automationId": "p"}]  # path 剥掉根级


def test_path_steps_drop_the_root_level_even_when_chain_holds_only_root():
    """链里只有根窗口一级 ⇒ 剥完为空 ⇒ 调用方不写 path（等价于「不收窄」）。"""
    from rpa_core.capture.desktop_agent import _path_steps_from

    root = FakeElement(FakeInfo(control_type="Window", automation_id="mainWindow"), handle=10)
    assert _path_steps_from([root], 10) == []


def test_narrow_by_path_rejects_a_level_matching_the_window_itself():
    """`_narrow_by_path` 只看后代：`path` 若（不规范地）指向 window 自身，则收窄为空。

    这是**有意**的——谓词保持干净。真机上不会再出现这种 path，因为捕获侧已经剥掉根级；
    这条判据把「谓词没被放宽」钉住，防止有人为了「兼容」重新把自身匹配加回来。
    """
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window", automation_id="mainWindow"))
    steps = [LocatorStep.model_validate({"controlType": "Window"})]
    assert cls._narrow_by_path(window, steps) == []


def test_narrow_by_path_still_finds_a_level_nested_under_the_window():
    """正例：path 的级是 window 的后代 ⇒ 正常收窄到该级。"""
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window", automation_id="mainWindow"))
    panel = FakeElement(FakeInfo(control_type="Pane", automation_id="panel-a"))
    window.children_result = [panel]
    steps = [LocatorStep.model_validate({"controlType": "Pane", "automationId": "panel-a"})]
    assert cls._narrow_by_path(window, steps) == [panel]


# --------------------------------------------------------------------------
# 执行器对 matchMode 的现状口径（D2 落码前的基线）
# --------------------------------------------------------------------------
def test_find_in_ignores_match_mode_when_absent():
    """未给 matchMode 时目标字段仍是等值比较（D2 兼容性硬要求）。"""
    cls = _uia_executor()
    scope = FakeElement(FakeInfo(control_type="Window"))
    exact = FakeElement(FakeInfo(control_type="Edit", automation_id="query"))
    partial = FakeElement(FakeInfo(control_type="Edit", automation_id="queryInput"))
    scope.children_result = [exact, partial]

    locator = DesktopLocator.model_validate(
        {"backend": "uia", "controlType": "Edit", "automationId": "query"}
    )
    assert cls._find_in(scope, locator) == [exact]


# --------------------------------------------------------------------------
# D2 纯函数（两执行器共用的匹配口径）
# --------------------------------------------------------------------------
def test_effective_match_mode_defaults_to_exact():
    from rpa_core.model.desktop import effective_match_mode

    assert effective_match_mode(None) == "exact"
    assert effective_match_mode("contains") == "contains"
    assert effective_match_mode("bogus") == "exact"  # 未知值回落 exact，不炸


def test_matches_text_exact_is_equality():
    from rpa_core.model.desktop import matches_text

    assert matches_text("query", "query", None) is True
    assert matches_text("queryInput", "query", None) is False


def test_matches_text_contains_is_substring():
    from rpa_core.model.desktop import matches_text

    assert matches_text("queryInput", "query", "contains") is True
    assert matches_text("input", "query", "contains") is False


def test_matches_text_regex_is_search_not_anchored():
    from rpa_core.model.desktop import matches_text

    assert matches_text("queryInput", "Input$", "regex") is True
    assert matches_text("queryBox", "Input$", "regex") is False


def test_matches_text_none_actual_never_matches():
    from rpa_core.model.desktop import matches_text

    assert matches_text(None, "x", "contains") is False
    assert matches_text(None, "x", None) is False


def test_matches_text_bad_regex_does_not_raise():
    """坏正则是数据问题——表现为「找不到元素」，不能让执行器崩。"""
    from rpa_core.model.desktop import matches_text

    assert matches_text("abc", "([", "regex") is False


# --------------------------------------------------------------------------
# D2 执行器侧：matchMode 真作用到 automationId / name
# --------------------------------------------------------------------------
def test_find_in_contains_widens_automation_id_match():
    cls = _uia_executor()
    scope = FakeElement(FakeInfo(control_type="Window"))
    partial = FakeElement(FakeInfo(control_type="Edit", automation_id="queryInput"))
    other = FakeElement(FakeInfo(control_type="Edit", automation_id="box"))
    scope.children_result = [partial, other]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "query",
            "matchMode": "contains",
        }
    )
    assert cls._find_in(scope, locator) == [partial]


def test_find_in_explicit_exact_stays_equality():
    """显式给 exact 与不给等价——两条路都不放宽。"""
    cls = _uia_executor()
    scope = FakeElement(FakeInfo(control_type="Window"))
    partial = FakeElement(FakeInfo(control_type="Edit", automation_id="queryInput"))
    scope.children_result = [partial]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "query",
            "matchMode": "exact",
        }
    )
    assert cls._find_in(scope, locator) == []


def test_find_in_contains_applies_to_name_too():
    cls = _uia_executor()
    scope = FakeElement(FakeInfo(control_type="Window"))
    hit = FakeElement(FakeInfo(control_type="Button", name="提交订单"))
    miss = FakeElement(FakeInfo(control_type="Button", name="取消"))
    scope.children_result = [hit, miss]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Button",
            "name": "提交",
            "matchMode": "contains",
        }
    )
    assert cls._find_in(scope, locator) == [hit]


def test_control_type_never_fuzzy():
    """controlType 恒等值：即使给了 contains 也不放宽（ADR 0018 §2 D2 定稿）。"""
    cls = _uia_executor()
    scope = FakeElement(FakeInfo(control_type="Window"))
    button = FakeElement(FakeInfo(control_type="Button"))
    list_item = FakeElement(FakeInfo(control_type="ListItem"))
    scope.children_result = [button, list_item]

    locator = DesktopLocator.model_validate(
        {"backend": "uia", "controlType": "Button", "matchMode": "contains"}
    )
    assert cls._find_in(scope, locator) == [button]


# --------------------------------------------------------------------------
# D2 执行器侧：win32 后端（matchMode 作用于 title；class_name 仍等值）
# --------------------------------------------------------------------------
class _Win32Info:
    """win32 ElementInfo 替身：`control_id` 是 **property**（非方法），M38 留档口径。"""

    def __init__(self, *, class_name=None, control_id=None):
        self.class_name = class_name
        self.control_id = control_id


class _Win32Element:
    """win32 控件替身：`descendants(**criteria)` + `window_text()` + `class_name()`。"""

    def __init__(self, text=None, *, class_name=None, control_id=None):
        self._text = text
        self._class = class_name
        self.element_info = _Win32Info(class_name=class_name, control_id=control_id)
        self.children_result: list[_Win32Element] = []

    def window_text(self):
        return self._text

    def class_name(self):
        return self._class

    def descendants(self, **criteria):
        out: list[_Win32Element] = []

        def walk(node: _Win32Element):
            for child in node.children_result:
                out.append(child)
                walk(child)

        walk(self)
        for key, value in criteria.items():
            if key == "title":
                out = [e for e in out if e._text == value]
            elif key == "class_name":
                out = [e for e in out if e._class == value]
        return out


def _win32_executor():
    from rpa_core.executors.desktop_win32 import Win32DesktopExecutor

    return Win32DesktopExecutor


def test_win32_find_in_contains_widens_title_match():
    """win32 侧 matchMode 作用于 title；未给时仍是等值（I8 注入的判据）。"""
    cls = _win32_executor()
    scope = _Win32Element()
    hit = _Win32Element("登录窗口 - 主界面")
    miss = _Win32Element("退出确认")
    scope.children_result = [hit, miss]

    locator = DesktopLocator.model_validate(
        {
            "backend": "win32",
            "title": "登录窗口",
            "matchMode": "contains",
        }
    )
    assert cls._find_in(scope, locator) == [hit]


def test_win32_find_in_without_match_mode_stays_equality():
    """win32 侧未给 matchMode ⇒ title 等值（D2 兼容性硬要求）。"""
    cls = _win32_executor()
    scope = _Win32Element()
    partial = _Win32Element("登录窗口 - 主界面")
    scope.children_result = [partial]

    locator = DesktopLocator.model_validate({"backend": "win32", "title": "登录窗口"})
    assert cls._find_in(scope, locator) == []


def test_win32_class_name_never_fuzzy():
    """win32 class_name 不参与 matchMode：给了 contains 也仍等值。"""
    cls = _win32_executor()
    scope = _Win32Element()
    exact = _Win32Element("t", class_name="Edit")
    longer = _Win32Element("t2", class_name="EditPlus")
    scope.children_result = [exact, longer]

    locator = DesktopLocator.model_validate(
        {"backend": "win32", "className": "Edit", "matchMode": "contains"}
    )
    assert cls._find_in(scope, locator) == [exact]


def test_win32_narrow_by_path_finds_a_level_under_the_window():
    """win32 侧同一口径：path 的级必须是 window 的后代；指向自身不算命中。

    win32 无 control_type / automationId 概念（`_step_matches` 对这两个键直接判不匹配），
    祖先链的刻画只能用 className / name（= 控件窗口文本）。
    """
    cls = _win32_executor()
    window = _Win32Element("RPA Core Desktop Demo", class_name="Window")
    panel = _Win32Element("panel", class_name="Panel")
    target = _Win32Element("Submit", class_name="Button")
    panel.children_result = [target]
    window.children_result = [panel]

    panel_step = [LocatorStep.model_validate({"className": "Panel"})]
    self_step = [LocatorStep.model_validate({"className": "Window"})]
    nope_step = [LocatorStep.model_validate({"className": "Nope"})]
    assert cls._narrow_by_path(window, panel_step) == [panel]
    assert cls._narrow_by_path(window, self_step) == []  # 指向自身 ⇒ 不收（只看后代）
    assert cls._narrow_by_path(window, nope_step) == []

    locator = DesktopLocator.model_validate(
        {
            "backend": "win32",
            "className": "Button",
            "title": "Submit",
            "path": [{"className": "Panel"}],
        }
    )
    assert cls._find(window, locator) == [target]


class _Rect:
    """pywinauto rect 的最小替身（_rect_contains / _rect_area 只读四个边）。"""

    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


# --------------------------------------------------------------------------
# D3 锚点起步：模型形状 + 运行期「先解析锚点，找不到报 ANCHOR_NOT_FOUND」
# --------------------------------------------------------------------------
def test_anchor_accepts_nested_locator():
    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "automationId": "target",
            "anchor": {"locator": {"backend": "uia", "automationId": "anchorEl"}},
        }
    )
    assert locator.anchor is not None
    assert locator.anchor.locator.automation_id == "anchorEl"


def test_anchor_is_optional():
    """未给 anchor ⇒ None，行为零变化（向后兼容）。"""
    locator = DesktopLocator.model_validate({"backend": "uia", "automationId": "t"})
    assert locator.anchor is None


def test_anchor_cannot_nest_anchor():
    """锚点不可嵌套——否则「解析顺序」变成无限递归入口。"""
    with pytest.raises(ValidationError):
        DesktopLocator.model_validate(
            {
                "backend": "uia",
                "automationId": "t",
                "anchor": {
                    "locator": {
                        "backend": "uia",
                        "automationId": "a",
                        "anchor": {"locator": {"backend": "uia", "automationId": "a2"}},
                    }
                },
            }
        )


def test_uia_find_raises_when_anchor_missing():
    """锚点找不到 ⇒ AnchorNotResolved（**不**回落成「直接找目标」）。"""
    from rpa_core.executors.desktop import AnchorNotResolved

    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    target = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    window.children_result = [target]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "q",
            "anchor": {"locator": {"backend": "uia", "automationId": "absent"}},
        }
    )
    with pytest.raises(AnchorNotResolved):
        cls._find(window, locator)


def test_uia_find_passes_when_anchor_present():
    """锚点在 ⇒ 正常返回目标（锚点只做前置确认，不改变目标匹配）。"""
    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    anchor_el = FakeElement(FakeInfo(control_type="Pane", automation_id="anchorEl"))
    target = FakeElement(FakeInfo(control_type="Edit", automation_id="q"))
    window.children_result = [anchor_el, target]

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "controlType": "Edit",
            "automationId": "q",
            "anchor": {"locator": {"backend": "uia", "automationId": "anchorEl"}},
        }
    )
    assert cls._find(window, locator) == [target]


def test_anchor_exception_carries_anchor_details():
    """异常携带锚点 dump，供调用点构造失败详情（不丢上下文）。"""
    from rpa_core.executors.desktop import AnchorNotResolved

    cls = _uia_executor()
    window = FakeElement(FakeInfo(control_type="Window"))
    window.children_result = []

    locator = DesktopLocator.model_validate(
        {
            "backend": "uia",
            "automationId": "q",
            "anchor": {"locator": {"backend": "uia", "automationId": "anchorEl"}},
        }
    )
    with pytest.raises(AnchorNotResolved) as exc_info:
        cls._find(window, locator)
    assert exc_info.value.details["anchor"]["automationId"] == "anchorEl"


def test_win32_find_raises_when_anchor_missing():
    """win32 后端同一口径（共用 AnchorNotResolved 类）。"""
    from rpa_core.executors.desktop import AnchorNotResolved

    cls = _win32_executor()
    scope = _Win32Element()
    target = _Win32Element("登录")
    scope.children_result = [target]

    locator = DesktopLocator.model_validate(
        {
            "backend": "win32",
            "title": "登录",
            "anchor": {"locator": {"backend": "win32", "title": "主窗口"}},
        }
    )
    with pytest.raises(AnchorNotResolved):
        cls._find(scope, locator)


def test_win32_find_passes_when_anchor_present():
    cls = _win32_executor()
    scope = _Win32Element()
    anchor_el = _Win32Element("主窗口")
    target = _Win32Element("登录")
    scope.children_result = [anchor_el, target]

    locator = DesktopLocator.model_validate(
        {
            "backend": "win32",
            "title": "登录",
            "anchor": {"locator": {"backend": "win32", "title": "主窗口"}},
        }
    )
    assert cls._find(scope, locator) == [target]
