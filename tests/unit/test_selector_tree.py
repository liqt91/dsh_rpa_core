"""selector_tree 纯函数判据：层级模型、勾选重组、转义。

为什么单独测：重组规则写错一个分支，用户勾出来的就是「保存成功但运行期永远
匹配不到」的死选择器——这类病只有逐规则的等值断言拦得住。
"""

from __future__ import annotations

from rpa_core.capture_kit.selector_tree import (
    attr_escape,
    compose,
    css_escape,
    level_included_after_toggle,
    levels_from_path,
    summary,
)

PATH = [
    {"tag": "div", "id": "app", "classes": ["page"], "nthOfType": None,
     "fragment": "#app"},
    {"tag": "div", "id": None, "classes": ["layout", "wide"], "nthOfType": None,
     "fragment": "div.layout"},
    {"tag": "li", "id": None, "classes": ["item"], "nthOfType": 3,
     "fragment": "li.item:nth-of-type(3)"},
    {"tag": "span", "id": None, "classes": [], "nthOfType": None,
     "fragment": "span"},
]


def test_levels_from_path_mirrors_capture_fragments() -> None:
    levels = levels_from_path(PATH)
    assert len(levels) == 4
    assert compose(levels) == "#app > div.layout > li.item:nth-of-type(3) > span"
    # 捕获侧只取首类：wide 初始不勾
    assert levels[1]["classes"][0]["on"] is True
    assert levels[1]["classes"][1]["on"] is False
    assert levels[1]["id"]["on"] is False and levels[0]["id"]["on"] is True


def test_used_classes_drive_initial_checks_when_present() -> None:
    """M105：捕获侧按「最能消歧的 class」累加，勾选态必须跟着 usedClasses 走。

    旧资产没有 usedClasses 时仍回退首类（上面那条测试钉住），开发期硬切不做迁移。
    """
    path = [
        {"tag": "li", "id": None, "classes": ["hotsearch-item", "pinned"],
         "nthOfType": None, "fragment": "li.hotsearch-item.pinned",
         "usedClasses": ["hotsearch-item", "pinned"]},
    ]
    levels = levels_from_path(path)
    assert [entry["on"] for entry in levels[0]["classes"]] == [True, True]
    assert compose(levels) == "li.hotsearch-item.pinned"


def test_incgs_marks_only_css_chain_included() -> None:
    """全链 path（目标 → 根）：只有 inCss 的层初始参与重组，与主 css 逐字一致。"""
    full = [
        {"tag": "html", "id": None, "classes": [], "nthOfType": None,
         "fragment": "html", "inCss": False},
        {"tag": "body", "id": None, "classes": ["dark"], "nthOfType": None,
         "fragment": "body.dark", "inCss": False},
        {"tag": "div", "id": "app", "classes": ["page"], "nthOfType": None,
         "fragment": "#app", "inCss": True},
        {"tag": "span", "id": None, "classes": ["t"], "nthOfType": None,
         "fragment": "span.t", "inCss": True},
    ]
    levels = levels_from_path(full)
    assert [level["included"] for level in levels] == [False, False, True, True]
    assert compose(levels) == "#app > span.t"
    # 用户往上勾一层：相邻判定按全链下标，body 与 #app 相邻 ⇒ 仍是子代连接
    levels[1]["included"] = True
    assert compose(levels) == "body.dark > #app > span.t"
    levels[0]["included"] = True
    levels[1]["included"] = False
    assert compose(levels) == "html #app > span.t"


def test_uncheck_middle_level_uses_descendant_not_child() -> None:
    levels = levels_from_path(PATH)
    levels[1]["included"] = False  # 勾掉中间层
    assert compose(levels) == "#app li.item:nth-of-type(3) > span"
    levels[2]["included"] = False  # 再勾一层
    assert compose(levels) == "#app span"


def test_id_dominates_level_fragment() -> None:
    levels = levels_from_path(PATH)
    levels[1]["id"] = {"on": True, "value": "main"}
    assert compose(levels) == "#app > #main > li.item:nth-of-type(3) > span"


def test_class_contains_mode_uses_attribute_selector() -> None:
    levels = levels_from_path(PATH)
    levels[1]["classes"][0]["mode"] = "包含"
    assert compose(levels) == '#app > div[class*="layout"] > li.item:nth-of-type(3) > span'


def test_nth_can_be_unchecked() -> None:
    levels = levels_from_path(PATH)
    levels[2]["nth"]["on"] = False
    assert compose(levels) == "#app > div.layout > li.item > span"


def test_target_level_always_participates() -> None:
    levels = levels_from_path(PATH)
    for level in levels:
        level["included"] = False
    assert compose(levels) == "span"


def test_escape_rules() -> None:
    assert css_escape("a b:c") == "a\\ b\\:c"
    assert attr_escape('a"b\\c') == 'a\\"b\\\\c'


def test_summary_renders_attributes() -> None:
    level = levels_from_path(PATH)[1]
    assert summary(level) == '<div class="layout wide">'


def test_target_level_checkbox_bounces_but_other_levels_follow_click() -> None:
    """目标级是必选项：点掉必须弹回；其余层严格跟随用户点击。"""
    assert level_included_after_toggle(4, 3, False) is True
    assert level_included_after_toggle(4, 3, True) is True
    assert level_included_after_toggle(4, 2, False) is False
    assert level_included_after_toggle(4, 2, True) is True
    assert level_included_after_toggle(0, 0, True) is True


def test_unchecked_level_contributes_nothing_even_with_attributes_on() -> None:
    """层级勾选是唯一门禁：取消层级后，这层属性勾着也不进选择器（M109）。

    用户报的就是这个：层级框已经取消，但那一层仍出现在选择器里——因为勾属性
    会反向把层拉回来。属性和层级是两件事，属性只决定「这层用哪些字段」。
    """
    levels = levels_from_path(PATH)
    # 中间层把 id/class/序号全勾上，只把层级本身取消
    levels[1]["included"] = False
    levels[1]["id"] = {"on": True, "value": "main"}
    levels[1]["classes"][1]["on"] = True
    assert compose(levels) == "#app li.item:nth-of-type(3) > span"
    assert "main" not in compose(levels) and "wide" not in compose(levels)
    # 重新勾上层，之前保留的属性勾选立刻恢复生效（值没被清掉）
    levels[1]["included"] = True
    assert compose(levels) == "#app > #main > li.item:nth-of-type(3) > span"


def test_compose_empty_path() -> None:
    assert compose([]) == ""
