"""GUI 中文化层契约测试（M54 S1）。

覆盖四层：
1. 解析层：``i18n.js`` 的四个块都能读出来（G4 起 ops 也从这里取）；
2. 三级回退：命令级覆盖 → 公共键 → 键名原文（与 Web ``app.js`` 的 ``fieldLabel`` 同口径）；
3. 元素槽位口径：GUI **有意**跟影刀叫「操作目标」，而不是 Web 的「选择器」/「定位器」；
4. 全覆盖门禁：**每个 manifest 的每个字段都有中文标签**——GUI 会把 properties 里
   所有字段渲染出来，缺一个就裸显示英文键名（这正是维护者那句「还带着 selector 参数」）。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import os

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.gui.i18n import (  # noqa: E402
    GUI_LABEL_OVERRIDES,
    field_label,
    load_command_display_names,
    load_command_field_labels,
    load_field_labels,
    load_op_labels,
)


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    return load_catalog(_commands_root())


# ---- 1. 解析层 -------------------------------------------------------------
def test_i18n_blocks_are_parsed():
    assert load_field_labels(), "fields 块没解析出来"
    assert load_command_field_labels(), "commandFields 块没解析出来"
    assert load_command_display_names(), "commands 块没解析出来"
    assert load_op_labels(), "ops 块没解析出来"


def test_command_names_have_a_single_source():
    """命令显示名只有一个解析入口：command_palette 那个是 gui.i18n 的同名 re-export。"""
    from rpa_core.gui.command_palette import load_command_display_names as from_palette

    assert from_palette() == load_command_display_names()


# ---- 2. 三级回退 -----------------------------------------------------------
def test_field_label_falls_back_three_levels():
    # ① 命令级覆盖：data.setVar 的 value 是「变量值」，公共键是「选择值」
    assert field_label("value", "data.setVar") == "变量值"
    assert field_label("value") == load_field_labels()["value"]
    # ② 公共键
    assert field_label("sessionId", "browser.click") == "会话 ID"
    # ③ 回退键名原文（与 Web fieldLabel 同口径，不假装有中文）
    assert field_label("no_such_field_xyz", "browser.click") == "no_such_field_xyz"


def test_command_scoped_labels_differ_for_same_key():
    """同名参数在不同命令下含义不同 ⇒ 标签必须不同（这正是 commandFields 的存在理由）。"""
    pairs = [
        ("value", "data.setVar", "browser.setValue"),
        ("name", "browser.cookieGet", "browser.setAttribute"),
        ("path", "browser.cookieGetAll", None),
    ]
    for key, cmd_a, cmd_b in pairs:
        if cmd_b is None:
            assert field_label(key, cmd_a) != field_label(key)
        else:
            assert field_label(key, cmd_a) != field_label(key, cmd_b)


# ---- 3. 元素槽位口径（G2） -------------------------------------------------
def test_element_slot_uses_yingdao_wording():
    """元素槽位在 GUI 里叫「操作目标」——与 Web **有意**不一致（M54 G2，维护者拍板）。

    这条不是「顺便检查」，而是**钉住有意的口径分歧**：谁把 ``GUI_LABEL_OVERRIDES``
    改回与 Web 一致（或删掉），它就会红，从而挡住「口径无声漂移」。
    """
    assert GUI_LABEL_OVERRIDES == {"selector": "操作目标", "locator": "操作目标"}
    assert field_label("selector", "browser.input") == "操作目标"
    assert field_label("locator", "desktop.findElement") == "操作目标"
    # 共享事实源本身没被改：Web 侧仍显示「选择器」（web 不用改）
    assert load_field_labels()["selector"] == "选择器"
    assert load_field_labels()["locator"] == "定位器"


# ---- 4. ops 单一来源（G4） -------------------------------------------------
def test_ops_labels_come_from_i18n_js():
    from rpa_core.gui.param_form import CONDITION_OP_LABELS, CONDITION_OPS

    labels = load_op_labels()
    assert CONDITION_OP_LABELS == labels, "GUI 的 op 标签与 i18n.js 漂移了"
    assert set(CONDITION_OPS) == set(labels)


# ---- 5. 全覆盖门禁（S3） ---------------------------------------------------
def test_every_manifest_field_has_chinese_label(catalog):
    """★ 门禁：**每个 manifest 的每个字段**都要有中文标签。

    只比 ``required`` 的顶层键是不够的（``test_i18n_fields_cover_required_input_schema_keys``
    就只比 required），而 GUI 把 properties 里**所有**字段都渲染出来——缺一个就裸显示
    英文键名。事实源 = i18n.js + commands/ 目录，两者都不在这里硬编码。
    """
    missing = []
    for command_id, manifest in sorted(catalog.items()):
        for key in manifest.input_schema.get("properties") or {}:
            if field_label(key, command_id) == key:
                missing.append(f"{command_id}.{key}")
    assert not missing, f"这些字段在 GUI 里会裸显示英文键名：{missing}"


def test_every_enum_field_has_value_labels(catalog):
    """★ 门禁（G3）：带 ``enum`` 的字段必须有 ``x-enum-labels``，且**键集合与 enum 相等**。

    要求相等而不是包含，是为了两个方向都拦得住：少一个键 ⇒ UI 里露出英文原值；
    多一个键 ⇒ 死标签（枚举里根本没有这个值，改了枚举忘了改标签）。
    ``x-enum-labels`` 是全端共享的事实源（Web ``app.js`` 与 GUI ``_build_enum`` 都读），
    所以这条判据既服务 GUI 也服务 Web。
    """
    problems = []
    for command_id, manifest in sorted(catalog.items()):
        for key, field in (manifest.input_schema.get("properties") or {}).items():
            if "enum" not in field:
                continue
            labels = field.get("x-enum-labels")
            if not labels:
                problems.append(f"{command_id}.{key}: 缺 x-enum-labels")
                continue
            diff = set(labels) ^ set(field["enum"])
            if diff:
                problems.append(
                    f"{command_id}.{key}: 标签键与枚举不匹配 {sorted(diff)}"
                )
    assert not problems, f"枚举值中文化有缺口/漂移：{problems}"


def test_param_form_labels_come_from_i18n(catalog, qapp):
    """端到端：参数面板**渲染出来**的标签就是 field_label 的结果（不是各写各的）。

    ★ 先钉一组**字面锚点**（不经过 ``field_label``）：否则 ``field_label`` 整体失效时，
    期望值与实际值一起退化成裸键名、两边同时变 ⇒ 判据恒真（2026-10-12 负向验证 I1
    实测到的假绿灯形态，正是「只比相等」的典型）。这组 must-have 就是那条独立证人。
    """
    from PySide6.QtWidgets import QLabel

    from rpa_core.gui.param_form import ParamForm

    manifest = catalog["browser.input"]
    form = ParamForm(
        manifest.input_schema, {}, manifest=manifest, element_provider=lambda: []
    )
    rendered = {label.text() for label in form.findChildren(QLabel)}

    # 独立证人：这几个是**写死**的中文标签，不随 field_label 一起退化
    assert {"会话 ID", "操作目标 *", "超时（毫秒）", "文本内容 *"} <= rendered
    # 英文键名不得作为标签出现（前缀切片把必填的 `xxx *` 形态一并覆盖）
    key_names = {"selector", "sessionId", "timeoutMs", "text", "mode"}
    assert not {t for t in rendered if t.split(" *")[0] in key_names}

    for key in manifest.input_schema.get("properties") or {}:
        expected = field_label(key, manifest.id)
        assert expected in rendered or f"{expected} *" in rendered, (
            f"{key} 的中文标签没进面板"
        )
