"""元素编辑器（M47.11 影刀式两页签 / 桌面 locator 字段化 / 就地结构校验）。

在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import base64
import os
import struct
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _one_pixel_png_url() -> str:
    """一张 1×1 的真 PNG（base64 data URL）。

    「陈旧截图被丢弃」这类判据需要一张**真能解码**的图——用假 base64 的话，
    判据会在解码那步就短路，分不清是「丢弃了」还是「本来就解不出来」。
    手写 PNG 字节而不是引Pillow：测试依赖越少越好。
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00", 6))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


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


def _browser_document() -> dict:
    """浏览器元素：主 css + 三条候选（含一条不唯一、一条未实测）。"""
    return {
        "kind": "browser",
        "selector": {
            "css": "#sb_form_q",
            "candidates": [
                {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
                {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 3},
                {"kind": "attribute", "selector": "[data-testid=search]"},  # 未实测
            ],
        },
        "verifyCount": 1,
        "metadata": {"tag": "textarea", "role": "searchbox", "url": "https://x/"},
    }


def _desktop_document() -> dict:
    """桌面元素：真机捕获产物形状（capture agent 只产 uia locator）。"""
    return {
        "kind": "desktop",
        "selector": {
            "locator": {
                "backend": "uia",
                "controlType": "Button",
                "automationId": "submitButton",
                "name": "Submit",
            }
        },
        "verifyCount": 1,
        "metadata": {
            "windowTitle": "RPA Core Desktop Demo",
            "controlType": "Button",
            "automationId": "submitButton",
            "name": "Submit",
            "className": "WindowsForms10.BUTTON.app.0.34f5582_r8_ad1",
        },
    }


# ---- 字段集按 backend 分（实测：两执行器消费的字段几乎不重叠） ----------------
def test_backend_field_sets_are_disjoint_and_match_the_executors(qapp):
    """字段表必须与执行器实际消费的字段一致；两个后端**只共享 matchMode**。

    这条是**事实钉子**：`desktop.py::_find` 读 automation_id / control_type / name /
    match_mode / path / anchor，`desktop_win32.py::_find` 读 title / class_name /
    class_name_re / control_id / found_index / match_mode / path / anchor。
    谁改了两边的消费面，这里必须跟着改（否则界面会提供死字段）。

    `matchMode` 是**唯一**被两个后端共用的消费字段（D2 扩面：uia 作用 name/automationId、
    win32 作用 title），故它是唯一允许出现在两张表里的键。
    """
    from rpa_core.gui.element_editor import fields_for

    uia = {key for key, _kind, _hint in fields_for("uia")}
    win32 = {key for key, _kind, _hint in fields_for("win32")}
    assert uia == {"controlType", "automationId", "name", "matchMode"}
    assert win32 == {
        "title", "className", "classNameRe", "controlId", "foundIndex", "matchMode",
    }
    assert uia & win32 == {"matchMode"}


def test_never_offers_fields_no_executor_consumes(qapp):
    """``menuPath`` / ``handle`` 两边都不消费 → 任何 backend 都不提供。

    ``menuPath`` 只作为 ``desktop_win32.menuSelect`` 的**命令输入**被读，从不来自
    locator；``handle`` 是运行期值。提供它们＝在 UI 上生产静默死字段。
    """
    from rpa_core.gui.element_editor import (
        ALL_FIELD_KEYS,
        declared_locator_keys,
        fields_for,
    )

    for backend in ("uia", "win32"):
        offered = {key for key, _kind, _hint in fields_for(backend)}
        assert "menuPath" not in offered
        assert "handle" not in offered
    assert "menuPath" not in ALL_FIELD_KEYS
    assert "handle" not in ALL_FIELD_KEYS
    # 声明键从模型取（不是手抄）：正因为模型真声明了 menuPath / handle，才需要标出它们
    declared = set(declared_locator_keys())
    assert {"backend", "menuPath", "handle", "foundIndex"} <= declared
    # 提供的字段必须是模型声明过的子集，否则组装出的 locator 会被 extra=forbid 拒掉
    assert set(ALL_FIELD_KEYS) <= declared


def test_field_tables_match_what_executors_actually_read(qapp):
    """字段表 ∪ 结构字段 vs 执行器**实际读取**的 locator 字段：双边必须逐字一致。

    这是「参数消费」在 UI 层的对应物，判据自维护：用 AST 扫执行器里全部
    ``locator.<字段>`` 读取点，再折回别名与界面字段表比对。

    - 表里有、执行器不读 → 界面在生产静默死字段（本次就抓出 uia 下 className 这种情况）
    - 执行器读、表里没有 → 用户永远改不了这个字段

    比对的是 **标量字段表 ∪ `STRUCTURED_LOCATOR_KEYS`**：`path`（层级列表）与
    `anchor`（嵌套 locator）的值不是标量，塞不进「一个输入框一个值」的表，由专门控件
    承载。为了避免 `STRUCTURED_LOCATOR_KEYS` 退化成「想加什么就加什么的豁免名单」，
    另有一条 `test_structured_keys_are_actually_read` 盯着它。

    任何一边变了这里就红，不需要有人记得同步两份清单。
    """
    import ast
    from pathlib import Path

    from rpa_core.gui.element_editor import STRUCTURED_LOCATOR_KEYS, fields_for
    from rpa_core.model.desktop import DesktopLocator

    snake_to_alias = {
        name: (field.alias or name)
        for name, field in DesktopLocator.model_fields.items()
    }
    executors = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "executors"
    for backend, filename in (("uia", "desktop.py"), ("win32", "desktop_win32.py")):
        tree = ast.parse((executors / filename).read_text(encoding="utf-8"))
        read: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "locator"
            ):
                read.add(node.attr)
        # model_dump 等非字段访问会落回原名，用字段表筛掉
        read_aliases = {
            snake_to_alias[name] for name in read if name in snake_to_alias
        }
        read_aliases.discard("backend")
        offered = {key for key, _kind, _hint in fields_for(backend)}
        offered |= set(STRUCTURED_LOCATOR_KEYS)
        assert offered == read_aliases, f"{backend}: 界面 {offered} vs 执行器 {read_aliases}"


def test_structured_keys_are_actually_read(qapp):
    """``STRUCTURED_LOCATOR_KEYS`` 里的每个键都必须被至少一个执行器真读。

    否则它就只是「豁免名单」里的一条——把某个字段从标量表里挪进来就能绕过上面那条
    契约，界面从此不提供、执行器也不消费，静默死字段换了个地方藏。
    """
    import ast
    from pathlib import Path

    from rpa_core.gui.element_editor import STRUCTURED_LOCATOR_KEYS
    from rpa_core.model.desktop import DesktopLocator

    snake_to_alias = {
        name: (field.alias or name)
        for name, field in DesktopLocator.model_fields.items()
    }
    executors = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "executors"
    read_aliases: set[str] = set()
    for filename in ("desktop.py", "desktop_win32.py"):
        tree = ast.parse((executors / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "locator"
                and node.attr in snake_to_alias
            ):
                read_aliases.add(snake_to_alias[node.attr])
    for key in STRUCTURED_LOCATOR_KEYS:
        assert key in read_aliases, f"结构字段 {key} 没有任何执行器读取它"


def test_compose_locator_ignores_cross_backend_fields(qapp):
    """给了对面后端的字段也不组装——界面漏一个字段不该写进死字段。"""
    from rpa_core.gui.element_editor import compose_locator

    assert compose_locator("uia", {}) == {"backend": "uia"}
    assert compose_locator("uia", {"className": "X", "title": "T"}) == {
        "backend": "uia"
    }
    assert compose_locator("win32", {"automationId": "X"}) == {"backend": "win32"}


def test_compose_locator_trims_and_casts_integers(qapp):
    from rpa_core.gui.element_editor import LocatorFieldError, compose_locator

    locator = compose_locator(
        "win32", {"title": " RPA Demo ", "controlId": " 42 ", "foundIndex": "0"}
    )
    assert locator == {
        "backend": "win32",
        "title": "RPA Demo",
        "controlId": 42,
        "foundIndex": 0,
    }

    with pytest.raises(LocatorFieldError) as err:
        compose_locator("win32", {"controlId": "not-a-number"})
    assert "controlId" in str(err.value)


def test_split_locator_only_returns_its_own_backend_fields(qapp):
    from rpa_core.gui.element_editor import split_locator

    assert split_locator(
        {"backend": "win32", "title": "T", "className": "C"}
    ) == {"title": "T", "className": "C"}
    assert split_locator({"backend": "uia", "className": "X"}) == {}


def test_inert_locator_keys_flags_dead_fields(qapp):
    from rpa_core.gui.element_editor import inert_locator_keys

    assert inert_locator_keys({"backend": "uia", "controlType": "Button"}) == []
    assert inert_locator_keys({"backend": "uia", "className": "X"}) == ["className"]
    # menuPath 在**两个**后端都是死字段（只作为命令输入被读）
    assert inert_locator_keys({"backend": "win32", "menuPath": ["a"]}) == ["menuPath"]


# ---- 模型报错的中文映射（把映射钉死，防止静默退回英文） ----------------------
def test_every_locator_model_rule_is_translated(qapp):
    """逐条触发 ``DesktopLocator`` 的规则，断言**每条都被译成中文**。

    这条是映射表的存在性证明：模型改了措辞、映射表没跟上 → 这里立刻红，
    而不是让用户突然看到一段英文 pydantic 报错。
    """
    from pydantic import ValidationError

    from rpa_core.gui.element_editor import translate_locator_message
    from rpa_core.model.desktop import DesktopLocator

    triggered = [
        {"backend": "abc"},
        {"backend": "uia"},
        {"backend": "win32"},
        {"backend": "win32", "className": "X", "classNameRe": "Y"},
        {"backend": "win32", "menuPath": []},
    ]
    seen = 0
    for case in triggered:
        with pytest.raises(ValidationError) as err:
            DesktopLocator.model_validate(case)
        for error in err.value.errors():
            raw = str(error.get("msg", error))
            seen += 1
            assert translate_locator_message(raw) != raw, raw
    assert seen == len(triggered)


def test_unknown_message_passes_through_untranslated(qapp):
    """译不出的消息原样返回——不吞、不编，用户至少能看到原文。"""
    from rpa_core.gui.element_editor import translate_locator_message

    assert translate_locator_message("something brand new") == "something brand new"


def test_locator_problems_reports_each_rule(qapp):
    from rpa_core.gui.element_editor import locator_problems

    assert locator_problems({"backend": "uia", "controlType": "Button"}) == []
    assert locator_problems({"backend": "uia"}) == [
        "uia 定位至少要勾一个身份字段（controlType / automationId / name）"
    ]
    assert locator_problems({"backend": "win32"}) == [
        "win32 定位至少要勾一个身份字段"
        "（title / className / classNameRe / controlId）"
    ]
    assert locator_problems(
        {"backend": "win32", "className": "X", "classNameRe": "Y"}
    ) == ["className（等值）与 classNameRe（正则）互斥，只能留一个"]


def test_css_problems_rejects_blank(qapp):
    from rpa_core.gui.element_editor import css_problems

    assert css_problems("#a") == []
    assert css_problems("   ") == ["主选择器（css）不能为空"]


# ---- 备选定位 UI 已移除，但数据必须保留（M47.11）-----------------------------


def test_candidate_promotion_helpers_are_retired(qapp):
    """``promotable`` / ``promote_candidate`` 随 UI 一起退役。

    判据钉的是「**没有调用方即死代码**」这条项目铁律：留着它们，日后有人看到
    ``promote_candidate`` 还在就以为候选提升功能仍可用（那正是维护者实测「几次捕捉
    都没看到有备选定位」的由来——功能入口与实际能力脱节）。
    """
    import rpa_core.gui.element_editor as mod

    assert not hasattr(mod, "promotable")
    assert not hasattr(mod, "promote_candidate")


def test_browser_form_has_no_candidate_widgets(qapp):
    """browser 分支不再摆候选列表 / 只读标签 / 提升按钮（三者都不存在，不是隐藏）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_browser_document())
    for gone in ("candidate_list", "candidates_label", "promote_button"):
        assert not hasattr(form, gone), gone
    assert not hasattr(form, "_sync_promote")
    assert not hasattr(form, "_promote")


def test_candidates_are_still_written_back(qapp):
    """**删界面 ≠ 删数据**：`selector.candidates` 原样带回文档。

    运行期自愈（``executors.browser._element_candidates``）按失败 selector 反查这份数据
    做回退；悄悄丢掉它等于悄悄拆掉 M28，且症状极隐蔽（元素在页面上还在，只是不再
    自我修复）。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    document = _browser_document()
    form = ElementEditorForm(document)
    result = form.result_document()
    assert result["selector"]["candidates"] == document["selector"]["candidates"]


def test_candidates_written_back_even_after_editing_css(qapp):
    """改过主 css 之后candidates 仍在（不是「只在没动过时才带」）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    document = _browser_document()
    form = ElementEditorForm(document)
    form.css_edit.setText("#changed")
    result = form.result_document()
    assert result["selector"]["css"] == "#changed"
    assert len(result["selector"]["candidates"]) == 3


# ---- 影刀式两页签布局（M47.11）-----------------------------------------------


def test_browser_form_has_preview_and_locate_tabs(qapp):
    """browser 分支是「预览 / 精准定位」两页签，且**没有** AI 辅助定位页签。

    AI 页签是维护者明确「先不做」的：摆一个点不开的空页签比不摆更糟，用户会以为
    功能坏了。所以判据正面钉住「页签数 == 2 且标题就是这两个」。

    **默认停在「精准定位」**（M47.12，维护者「默认展示精准定位便签页」）：用户进
    编辑器的主诉求是改选择器，树 + 属性表才是主力，截图是改完之后的确认手段。
    顺带还有个好处：默认页不是预览 ⇒ 开框不会顺手拍一张几百 KB 的 PNG。
    """
    from rpa_core.gui.element_editor import ElementEditorForm, PreviewShot

    form = ElementEditorForm(_path_document())
    titles = [form.tabs.tabText(i) for i in range(form.tabs.count())]
    assert titles == ["预览", "精准定位"]
    assert isinstance(form.tabs.widget(0), PreviewShot)
    assert form.tabs.widget(1) is form.locate_page
    assert form.tabs.currentIndex() == 1  # 默认精准定位，不是预览


def test_selector_choice_and_anchor_are_greyed_out(qapp):
    """底部「默认选择器 / XPath」与「锚点 + 添加」都摆出来，但后两者**置灰**。

    XPath 是维护者点名要的（相比影刀少了它），所以不能装作没有；但全链路只认 css，
    做成能点的就是假功能。锚点同理——browser 元素没有运行期消费方。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    assert form.selector_default_radio.isChecked()
    assert form.selector_xpath_radio.isEnabled() is False
    assert form.selector_xpath_radio.toolTip()
    assert form.anchor_add_button.isEnabled() is False
    assert form.anchor_add_button.toolTip()


def test_screenshot_only_requested_when_switching_to_preview(qapp):
    """**只有切到「预览」页签才截图**：改 css / 切到精准定位都不触发。

    每敲一个字符就截一张 base64 PNG 既慢又占内存，而用户在精准定位页里改选择器时
    根本不看图。判据用「请求条数」而不是「有没有请求」——把 ``want_shot`` 恒true
    也会让「有没有请求」这条判据假绿。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    calls: list[dict] = []

    def preview(_css):
        return {"count": 1}

    def clear():
        return {"count": 0}

    def shot(css, **_kwargs):
        calls.append({"css": css})
        return {"count": 1, "dataUrl": "", "rect": None, "viewport": {}}

    form = ElementEditorForm(_path_document())
    form.enable_live_preview(preview, clear, shot)
    form._preview_timer.stop()  # 别让防抖预跑搅进来

    form.css_edit.setText("#typed")
    form._run_preview()
    assert calls == []

    # 走**真实信号**（setCurrentIndex 会发currentChanged），不手工调 _on_tab_changed
    # ——手工调会与信号各触发一次，把「请求条数」这条判据变成数信号次数的假绿。
    form.tabs.setCurrentIndex(1)
    assert calls == []

    form.tabs.setCurrentIndex(0)
    assert calls == [{"css": "#typed"}]


def test_preview_shot_without_channel_says_so_instead_of_blank(qapp):
    """没接截图通道时，「预览」页**仍摆出来**（影刀那两页的结构），但要有话说。

    藏掉整页比「这一页暂时没内容」更难解释——用户要的就是那两页的布局。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.enable_live_preview(lambda _css: {"count": 1}, lambda: {"count": 0})
    form._preview_timer.stop()
    form._run_shot()
    assert not form.preview_shot.has_shot
    assert "未接入截图通道" in form.preview_shot.message


def test_shot_failure_does_not_touch_hit_label(qapp):
    """截图失败**不改命中标签**：那是「校验失败」，而截图只是观感增强。

    这条与 host 侧 ``test_want_shot_failure_still_returns_count`` 配对：截图链路任何
    一环坏掉，都只该让预览页显示一句原因，不该把「命中 N 个」这条真判据打成错误。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.enable_live_preview(
        lambda _css: {"count": 1}, lambda: {"count": 0}, lambda _css, **_k: {"count": 1}
    )
    form.set_hit_label("命中 1 个（页面上已黄框高亮）", "#1a7f37")
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "error": "no-active-tab"})
    assert form.preview_label.text() == "命中 1 个（页面上已黄框高亮）"
    assert "预览截图失败" in form.preview_shot.message


def test_shot_without_data_url_distinguishes_zero_hit(qapp):
    """要了图却没拿到：**区分五种失败**，用户动作完全不同。

    - 没命中 ⇒ 改选择器；
    - **跑的是旧扩展快照** ⇒ 去 chrome://extensions 点刷新（2026-10-09 真机反馈补）；
    - 命中了但**扩展没给窗口句柄** ⇒ 切到那个浏览器窗口再预览；
    - 有句柄但**截屏失败**（最小化/锁屏）⇒ 换个窗口状态。

    混成一句「预览失败」等于让用户自己猜（这正是 M47.11 之前那句「浏览器未返回
    截图」的问题：它把「没命中」和「截不到」说成了一件事）。

    ``ext-stale`` 单独一档的理由：它的现场症状是**红框/校验全正常、只有新功能没生效**
    （截图走host 桌面坐标截屏是新加的），最容易被当成「代码坏了」，而处置只是点一下刷新。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.enable_live_preview(
        lambda _css: {"count": 1}, lambda: {"count": 0}, lambda _css, **_k: {"count": 1}
    )
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "count": 0})
    assert "未命中" in form.preview_shot.message

    # 浏览器里跑的是旧扩展快照：新字段读不到 ⇒ 没有句柄
    form._shot_seq += 1
    form._on_shot_done(
        {"seq": form._shot_seq, "count": 1, "shotError": "ext-stale", "extBuild": "0.6.3"}
    )
    assert "旧版扩展" in form.preview_shot.message
    assert "0.6.3" in form.preview_shot.message
    assert "chrome://extensions" in form.preview_shot.message

    # 命中了但扩展没回窗口句柄（拿不到窗口矩形 ⇒ 截不了）
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "count": 3, "shotError": "no-window-handle"})
    assert "未能定位到浏览器窗口" in form.preview_shot.message

    # 有句柄但截屏本身失败
    form._shot_seq += 1
    form._on_shot_done(
        {
            "seq": form._shot_seq,
            "count": 2,
            "windowHandle": 123,
            "shotError": "shot-failed",
        }
    )
    assert "截图失败" in form.preview_shot.message


def test_stale_shot_reply_is_discarded(qapp):
    """陈旧截图回传被丢弃：用户已切走或又改了 css，旧图不该盖上去。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.enable_live_preview(
        lambda _css: {"count": 1}, lambda: {"count": 0}, lambda _css, **_k: {"count": 1}
    )
    form._shot_seq += 1
    form._on_shot_done(
        {
            "seq": form._shot_seq - 1,
            "count": 1,
            "dataUrl": _one_pixel_png_url(),
            "rect": {"left": 0, "top": 0, "width": 1, "height": 1},
            "viewport": {"width": 10, "height": 10, "dpr": 1},
        }
    )
    assert not form.preview_shot.has_shot


def test_decode_shot_rejects_junk_without_raising(qapp):
    """坏 data URL 一律返回 None，**绝不抛**：一张坏图不该把命中判据一起带崩。"""
    from rpa_core.gui.element_editor import decode_shot

    assert decode_shot(None) is None
    assert decode_shot(123) is None
    assert decode_shot("no-comma-here") is None
    assert decode_shot("data:text/plain;base64,AAAA") is None
    assert decode_shot("data:image/png;base64,@@@") is None
    assert decode_shot("data:image/png;base64,") is None
    assert decode_shot("data:image/png;base64,AAAA") is None  # 不是合法 PNG


def test_locate_page_explains_missing_path(qapp):
    """老元素没有 path：「精准定位」页**说清为什么空**，不是一片空白。

    空白的页签会被当成「加载失败」或「这元素不支持精准定位」。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm({"kind": "browser", "selector": {"css": "#a"}})
    assert form.path_list is None
    labels = [
        label.text()
        for label in form.locate_page.findChildren(type(form.info_label))
        if label.text()
    ]
    assert any("没有捕获时的节点路径" in text for text in labels)


def test_desktop_form_has_preview_and_locate_tabs(qapp):
    """桌面分支**也有**「预览 / 精准定位」两页签（M47.12 推翻 M47.11 的结论）。

    M47.11 的判据写的是「桌面分支不摆这两个页签」，理由是「它没有页面可截图」。
    M47.12 实测否掉了这条理由：桌面腿截的是**窗口/控件**（win32 ``GetWindowRect``
    + 坐标截屏），而且 ``capture_as_image`` 走屏幕 BitBlt 这条路本来就在执行器里
    用了（``desktop.screenshot`` 命令）——**不是不存在的能力，是没接到编辑区**。
    统一到桌面坐标截屏后，桌面与浏览器共用同一个 ``PreviewShot`` 控件，
    不摆页签反而让「两条腿长得不一样」。

    仍然**不摆**的是浏览器专属那两样：XPath 单选与锚点「添加」（桌面腿的锚点是
    ``_build_anchor_view``，真能用；XPath 对两条腿都还没有消费方）。
    """
    from rpa_core.gui.element_editor import ElementEditorForm, PreviewShot

    form = ElementEditorForm(
        {"kind": "desktop", "selector": {"locator": {"backend": "win32", "controlId": 3}}}
    )
    titles = [form.desktop_tabs.tabText(i) for i in range(form.desktop_tabs.count())]
    assert titles == ["预览", "精准定位"]
    assert isinstance(form.desktop_tabs.widget(0), PreviewShot)
    # 默认也停在「精准定位」（与browser 腿同一口径）。
    assert form.desktop_tabs.currentIndex() == 1
    # 浏览器专属的两样不摆。
    assert not hasattr(form, "selector_xpath_radio")
    assert not hasattr(form, "anchor_add_button")


def test_desktop_preview_screenshot_takes_no_css_argument(qapp):
    """桌面腿的截图通道**无参调用**：元素就是捕获时那一个，没有 css 可传。

    这条钉住 :meth:`_run_shot` 里的分流——browser 传 ``(css,)``、desktop 传 ``()``。
    如果哪天桌面也要求传选择器，这里会红，而红的方向就是「调用点与签名又对不上」
    （M47.11 就在``FakeEditor`` 签名没跟上时集体红过一次）。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(
        {"kind": "desktop", "selector": {"locator": {"backend": "win32", "controlId": 3}}}
    )
    calls: list[tuple] = []

    def shot():
        calls.append(())
        return {"shotError": "shot-failed"}

    form.enable_shot_channel(shot)
    form._run_shot()
    # 截图走**工作线程**⇒ 必须等信号回来。
    #
    # 这里刻意**不用** `QSignalSpy.wait()`：它在单文件跑时绿、全量跑时红（实测
    # `spy.wait(2000)` 返回 False）——阻塞式等待在整套件跑、别的用例留了未清的事件
    # 循环时不可靠。改成与 `test_gui_panels.py` 里 `pump_until` 同一套写法：泵事件 +
    # 轮询条件。**判据自身的稳定性也是判据质量的一部分**——一条时绿时红的判据
    # 会把真回归淹没在噪声里。
    import time as _time

    def pump_until(predicate, timeout: float = 10.0) -> bool:
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            qapp.processEvents()
            if predicate():
                return True
            _time.sleep(0.02)
        return False

    assert pump_until(lambda: calls == [()]), "桌面截图通道没被无参调用"
    assert calls == [()], f"桌面截图通道必须能无参调用，实际 {calls}"


def test_path_tree_and_attr_table_are_equal_width(qapp):
    """节点树与属性表**均分**（M47.12，维护者「节点路径和属性框均分即可」）。

    M47.10 是 1:2（``setSizes([180, 360])``），理由是「属性表 4 列需要更宽」。实测
    树那几行加上类型与勾选属性之后并不窄，而 1:2 让树右侧大片留白、显得空。
    """
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.show()
    form.resize(900, 700)
    form.path_splitter.setSizes([450, 450])
    sizes = form.path_splitter.sizes()
    assert len(sizes) == 2
    # **不能断言严格相等**：splitter 有 handle（默认几 px），两栏总宽要减掉它，
    # 实测均分请求拿到的是 [438, 437]。钉「差值不超过 handle 宽」才是「均分」的
    # 真实语义——写成 `sizes[0] == sizes[1]` 只会得到一条永远红的假判据。
    handle = form.path_splitter.handleWidth()
    assert abs(sizes[0] - sizes[1]) <= handle + 1, (
        f"两栏应均分（差值≤handle 宽 {handle}），实际 {sizes}"
    )
    # 反向：偏离均分（例如 1:2）必须超出差值上限，否则这条判据抓不到真回归
    form.path_splitter.setSizes([180, 720])
    skewed = form.path_splitter.sizes()
    assert abs(skewed[0] - skewed[1]) > handle + 1, f"1:2 不该被判成均分，实际 {skewed}"


def test_path_splitter_stretch_factors_keep_columns_equal():
    """``setStretchFactor`` 也要均分（用户拖宽窗口后多出的宽度怎么分）。

    上一条只钉了 ``setSizes``（**初始**比例），漏了另一半：``setStretchFactor``
    决定用户拉宽窗口后多出来的宽度怎么分。少钉这一半，注入
    ``setStretchFactor(1, 2)`` 会**全绿**——但真机上用户一拖宽窗口，属性表立刻
    比节点路径宽一倍，正是「太挤了」那条诉求的复发。

    **为什么在源码层钉、而不是读运行时属性**（两条都实测过，不是猜的）：

    1. **读取侧不存在**：``QSplitter.stretchFactor()`` 在 PySide6 里**没有**——
       只有 ``setStretchFactor``。写 ``splitter.stretchFactor(i)`` 拿到的是
       ``AttributeError``（PySide6 还会提示 "Did you mean: 'setStretchFactor'?"）。
    2. **运行时也测不到**：对裸 QSplitter 实测，``f=(1,1)`` 与 ``f=(1,2)`` 在
       ``resize(1400)`` + ``processEvents()`` 之后 sizes **完全相同**（都还是
       ``[318, 318]``）——offscreen 下 ``resize`` 不触发 splitter 重分配。

    ⇒ 离屏环境里「拉宽后仍均分」根本不可观测，只能钉配置本身。这与
    ``test_field_tables_match_what_executors_actually_read`` 是同一类妥协；
    判据要写清**为什么只能这样**，免得后人以为漏了运行时验证。
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "rpa_core" / "gui" / "element_editor.py"
    ).read_text(encoding="utf-8")
    pairs = re.findall(r"splitter\.setStretchFactor\((\d+),\s*(\d+)\)", source)
    assert pairs, "没找到 setStretchFactor 调用（splitter 布局代码被改名/挪走了？）"
    factors = [int(value) for _index, value in pairs]
    assert factors == [1, 1], (
        f"两栏 stretch factor 应都是 1（新增宽度平均分），实际 {factors}——"
        f"改成 1:2 的话，用户一拉宽窗口属性表就比节点路径宽一倍"
    )


def test_dialog_minimum_width_is_wide_enough(qapp):
    """两个窗口的最小宽度都≥ 860（M47.12「整个捕获确认窗口可以宽一点」）。

    同时钉住 :func:`_open_wide_enough`：**已保存的旧窄尺寸也要被顶宽**——否则用户
    上一次存过 560 宽，下次打开仍是窄的（持久化会盖过新的默认值）。
    """
    from rpa_core.gui.element_editor import (
        _DIALOG_MIN_WIDTH,
        ElementEditorForm,
        _open_wide_enough,
    )

    assert _open_wide_enough(None)[0] == _DIALOG_MIN_WIDTH
    assert _open_wide_enough((560, 640))[0] == _DIALOG_MIN_WIDTH
    assert _open_wide_enough((1200, 900))[0] == 1200  # 已经够宽就沿用
    # 高度不设下限（矮只是少看几行）
    assert _open_wide_enough((1200, 400))[1] == 400
    # 纯函数别依赖 Qt 也能算
    assert _DIALOG_MIN_WIDTH >= 860
    assert ElementEditorForm is not None


def test_node_label_shows_tag_and_checked_attrs(qapp):
    """树行显示**节点类型 + 勾选的属性**（M47.12，影刀那一屏的行样式）。

    影刀摆的是 ``div#kw.s-hotsearch-content`` 这样；我们此前摆的是编译后的
    ``fragment``（同信息但看不出「哪个属性是勾上的」，要跟右侧属性表来回对照）。
    """
    from rpa_core.gui.element_editor import node_label

    # id 等值命中 ⇒ 整层就是 #id（与 compile_fragment 同口径）
    assert node_label({"tag": "div", "id": "kw", "fragment": "#kw"}) == "div#kw"
    # tag + 勾中的 class + nth
    assert (
        node_label(
            {"tag": "li", "classes": ["a", "b"], "nthOfType": 2, "fragment": "li.a:nth-of-type(2)"}
        )
        == "li.a:nth-of-type(2)"
    )
    # 未勾选的 class 不显示（那是用户要去右半勾的东西，显示出来等于暗示它已生效）
    assert (
        node_label({"tag": "span", "classes": ["x", "y"], "fragment": "span.x"})
        == "span.x"
    )
    # 没有 tag 也不该返回空串
    assert node_label({"fragment": ""}) == "?"


# ---- 节点树（M44 S5）：勾层级 → 按fragment 拼回主选择器 ----------------------
def _path_document() -> dict:
    """带祖先链的浏览器元素：path 形状与 content.js ``pathFor`` 的产出一致。"""
    return {
        "kind": "browser",
        "selector": {
            "css": "body > div.wrap > button.ok:nth-of-type(2)",
            "path": [
                {"tag": "body", "id": None, "classes": [],
                 "nthOfType": None, "fragment": "body"},
                {"tag": "div", "id": None, "classes": ["wrap"],
                 "nthOfType": 1, "fragment": "div.wrap"},
                {"tag": "button", "id": None, "classes": ["ok"],
                 "nthOfType": 2, "fragment": "button.ok:nth-of-type(2)"},
            ],
        },
        "verifyCount": 1,
        "metadata": {"tag": "button"},
    }


def test_path_tree_composes_css_from_checked_levels(qapp):
    """勾选层级决定主选择器：取消祖先 → css 变成剩余层级的 join。

    影刀节点树的用途正在这里：6 级全路径脆，截短成末两级往往更稳 —— 但此前
    用户只能对着 `css` 一行文本手写，捕获到的层级信息**有数据没入口**。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    assert form.path_list.count() == 3
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    ), "css 是全路径 → 建树时应当全勾"

    form.path_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert form.css_edit.text() == "div.wrap > button.ok:nth-of-type(2)"
    assert not form.blocked


def test_path_tree_leaf_cannot_be_unchecked(qapp):
    """末级（目标本身）取消勾选会被勾回——「不指向目标的定位方案」不成立。"""
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.path_list.item(2).setCheckState(Qt.CheckState.Unchecked)
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked
    assert form.css_edit.text() == "body > div.wrap > button.ok:nth-of-type(2)"


def test_path_tree_derives_checks_from_shortened_css(qapp):
    """重开元素时按 css 反推勾选态（css 是路径后缀 → 只勾后缀）。

    不做双向同步（css 解析回层级不可靠），但**建树这一次**必须让勾选态与
    落盘的 css 一致：否则用户上轮截短的祖先，这轮随便点一下就被全路径覆盖。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    doc = _path_document()
    doc["selector"]["css"] = "div.wrap > button.ok:nth-of-type(2)"
    form = ElementEditorForm(doc)
    assert form.path_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert form.path_list.item(1).checkState() == Qt.CheckState.Checked
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked

    # css 不出自这条路径（如候选提升来的）：回退全勾，下次勾选才重写
    doc["selector"]["css"] = 'input[name="q"]'
    form = ElementEditorForm(doc)
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    )


def test_path_tree_absent_without_path(qapp):
    """老元素没有 path：不建树，但**要说清为什么空**（不是摆个空壳）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_browser_document())
    assert form.path_list is None          # 没有控件，不是空控件
    assert form.attr_table is None
    labels = [
        label.text()
        for label in form.locate_page.findChildren(type(form.info_label))
        if label.text()
    ]
    assert any("没有捕获时的节点路径" in text for text in labels)


def test_path_survives_edit_roundtrip(qapp):
    """编辑（含勾层级重写 css）不得抹掉 `selector.path` —— 它是树的原料。

    回归口径同 candidates：``result_document`` 以原 selector 为基底覆盖被编辑的键，
    界面上没有直接编辑 path 的地方，写回必须原样保留。
    """

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.css_edit.setText("button.ok:nth-of-type(2)")
    selector = form.result_document()["selector"]
    assert selector["css"] == "button.ok:nth-of-type(2)"
    assert selector["path"] == _path_document()["selector"]["path"]


def test_editor_browser_blocks_blank_css(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_browser_document(), name="searchBox")
    dialog.css_edit.setText("   ")
    assert "不能为空" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.css_edit.setText("#q")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_editor_browser_output_passes_the_real_contract(qapp):
    """产出必须能被真实契约接受（模型 + selector_errors），编辑器不承诺别的。"""
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.model.capture import selector_errors, validate_element_document

    dialog = ElementEditorDialog(_browser_document(), name="searchBox")
    dialog.css_edit.setText('input[name="q"]')
    dialog.accept()
    element = validate_element_document(dialog.result_document())
    assert selector_errors(element) == []
    assert element.metadata["role"] == "searchbox"  # 捕获的元数据原样保留


# ---- 对话框：桌面 -------------------------------------------------------------
def test_editor_desktop_shows_only_the_active_backend_rows(qapp):
    """uia 元素只显示 uia 的行；**对面后端独有**的行隐藏（提供它们＝生产死字段）。

    `matchMode` 是唯一被两个后端共用的字段（D2 扩面），故它在切 backend 时**保持可见**——
    断言写成「对面后端**独有**的键才隐藏」，而不是「两集合不相交」（后者在 matchMode
    引入后不成立，且它本就是隐含假设、不是这条测试要断的东西）。
    """
    from rpa_core.gui.element_editor import ElementEditorDialog, fields_for

    uia = {k for k, _kd, _h in fields_for("uia")}
    win32 = {k for k, _kd, _h in fields_for("win32")}
    shared = uia & win32

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    assert dialog.backend_combo.currentText() == "uia"
    for key in uia:
        assert dialog.field_boxes[key].isVisibleTo(dialog), key
    for key in win32 - shared:
        assert not dialog.field_boxes[key].isVisibleTo(dialog), key

    dialog.backend_combo.setCurrentText("win32")
    for key in win32:
        assert dialog.field_boxes[key].isVisibleTo(dialog), key
    for key in uia - shared:
        assert not dialog.field_boxes[key].isVisibleTo(dialog), key


def test_editor_desktop_prefills_from_locator_and_metadata(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    # locator 里有的字段：勾上
    assert dialog.field_boxes["controlType"].isChecked()
    assert dialog.field_boxes["automationId"].isChecked()
    assert dialog.field_edits["name"].text() == "Submit"
    # locator 里没有、但捕获回传过 className：值预填好，只是没勾（等用户决定）
    assert not dialog.field_boxes["className"].isChecked()
    assert dialog.field_edits["className"].text().startswith("WindowsForms10.BUTTON")
    # 未勾的字段不进 locator
    assert "className" not in dialog.result_document()["selector"]["locator"]

    dialog.accept()
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["automationId"] == "submitButton"
    assert locator["backend"] == "uia"


def test_editor_desktop_switching_backend_rewrites_the_locator(qapp):
    """换 backend 等于换一套定位机制：字段不通用，locator 随之重写并提示死字段。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    # uia 下 className 是死字段（执行器读不到）——预填了值但没勾，所以不在 locator 里
    assert "className" not in dialog._compose()
    dialog.backend_combo.setCurrentText("win32")
    # 换成 win32 之后，uia 的三行不入 locator，模型立刻报「至少一个身份字段」
    assert dialog._compose() == {"backend": "win32"}
    assert "至少要勾一个身份字段" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.field_boxes["classNameRe"].setChecked(True)
    dialog.field_edits["classNameRe"].setText(r"WindowsForms10\.BUTTON\..*")
    assert dialog.info_label.text() == ""
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_document()["selector"]["locator"] == {
        "backend": "win32",
        "classNameRe": r"WindowsForms10\.BUTTON\..*",
    }


def test_editor_desktop_flags_inert_fields_from_the_original_locator(qapp):
    """原 locator 里的死字段要在界面里说出来（保存会移除，不是静默改用户文件）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    document = _desktop_document()
    document["selector"]["locator"]["menuPath"] = ["文件", "新建"]
    dialog = ElementEditorDialog(document, name="submit")
    assert "menuPath" in dialog.field_hint.text()
    assert "保存会移除" in dialog.field_hint.text()
    # 死字段确实不会被写回
    assert "menuPath" not in dialog.result_document()["selector"]["locator"]


def test_editor_desktop_blocks_mutually_exclusive_class_fields(qapp):
    """className / classNameRe 互斥必须**当场**拦下，而不是等保存才报错。"""
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.backend_combo.setCurrentText("win32")
    dialog.field_boxes["className"].setChecked(True)
    dialog.field_boxes["classNameRe"].setChecked(True)
    dialog.field_edits["classNameRe"].setText(r"WindowsForms10\.BUTTON.*")

    assert "互斥" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    # 取消其中一个即可保存，且 locator 里只剩留下的那个
    dialog.field_boxes["className"].setChecked(False)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    locator = dialog.result_document()["selector"]["locator"]
    assert "className" not in locator
    assert locator["classNameRe"] == r"WindowsForms10\.BUTTON.*"


def test_editor_desktop_blocks_bad_integer_and_missing_identity(qapp):
    from PySide6.QtWidgets import QDialog

    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.backend_combo.setCurrentText("win32")
    dialog.field_boxes["controlId"].setChecked(True)
    dialog.field_edits["controlId"].setText("abc")
    assert "整数" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted

    # 把身份字段全清掉（win32 侧）：模型要报「至少一个身份字段」，界面也必须拦
    dialog.field_boxes["controlId"].setChecked(False)
    for key in ("title", "className", "classNameRe"):
        dialog.field_boxes[key].setChecked(False)
    assert "至少要勾一个" in dialog.info_label.text()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_editor_desktop_output_passes_the_real_contract(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.model.capture import selector_errors, validate_element_document

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.accept()
    element = validate_element_document(dialog.result_document())
    assert selector_errors(element) == []


def test_editor_preserves_keys_it_does_not_expose(qapp):
    """界面没暴露的 selector 子键（如自定义扩展字段）必须原样保留。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    document = _browser_document()
    document["selector"]["note"] = "手工加的备注"
    dialog = ElementEditorDialog(document, name="searchBox")
    dialog.accept()
    result = dialog.result_document()
    assert result["selector"]["note"] == "手工加的备注"
    assert result["selector"]["candidates"] == document["selector"]["candidates"]


# ---- 元素库入口 ---------------------------------------------------------------
def _fake_editor(result_document: dict | None):
    """替身编辑器。必须是 QWidget：app 侧用 ``present_window`` 置顶对话框，
    那一串调用（setWindowFlag/show/raise_/alert）在纯 Python 类上会 AttributeError。
    """
    from PySide6.QtWidgets import QDialog

    class FakeEditor(QDialog):
        seen: list[tuple[str, dict]] = []

        def __init__(
            self, document, *, name,
            preview_css=None, clear_preview_css=None, shot_css=None, parent=None,
        ):
            super().__init__(parent)
            type(self).seen.append((name, document))

        def exec(self):
            if result_document is None:
                return QDialog.DialogCode.Rejected
            return QDialog.DialogCode.Accepted

        def result_document(self):
            return result_document

    FakeEditor.seen = []
    return FakeEditor


def test_element_library_wires_the_edit_entry(window, monkeypatch):
    """面板要有「编辑」按钮，且双击走同一条路径（不出现行为分叉）。"""
    from rpa_core.gui import element_editor

    window._save_named_flow("ed1")
    window.save_element_descriptor("searchBox", _browser_document())
    window._toggle_elements_dock()
    window._refresh_elements()
    panel = window._element_panel
    assert panel.edit_button.text() == "编辑"
    assert "不用手写" in panel.edit_button.toolTip()

    fake = _fake_editor(None)
    monkeypatch.setattr(element_editor, "ElementEditorDialog", fake)
    panel.list.setCurrentRow(0)
    panel.edit_button.click()
    panel.list.itemDoubleClicked.emit(panel.list.item(0))
    # 按钮与双击都必须带着**选中元素的真实名**进编辑器
    assert [name for name, _doc in fake.seen] == ["searchBox", "searchBox"]


def test_edit_element_writes_back(window, monkeypatch):
    """编辑器保存 → 文档落盘（通过 app 的真实入库路径）。"""
    from rpa_core.gui import element_editor

    window._save_named_flow("ed2")
    window.save_element_descriptor("searchBox", _browser_document())

    fake = _fake_editor(
        {
            "kind": "browser",
            "selector": {"css": "#promoted"},
            "verifyCount": 3,
            "metadata": {"role": "searchbox"},
        }
    )
    monkeypatch.setattr(element_editor, "ElementEditorDialog", fake)
    window._edit_element("searchBox")

    # 打开时喂进去的是**盘上的当前文档**（否则编辑器显示的可能不是实际存的）
    assert fake.seen[0][1]["selector"]["css"] == "#sb_form_q"

    stored = window._element_store().read("searchBox")
    assert stored["selector"]["css"] == "#promoted"
    assert stored["verifyCount"] == 3
    assert "已保存元素 searchBox" in window.statusBar().currentMessage()


def test_edit_element_cancel_changes_nothing(window, monkeypatch):
    from rpa_core.gui import element_editor

    window._save_named_flow("ed3")
    window.save_element_descriptor("searchBox", _browser_document())

    monkeypatch.setattr(element_editor, "ElementEditorDialog", _fake_editor(None))
    window._edit_element("searchBox")
    assert window._element_store().read("searchBox")["selector"]["css"] == "#sb_form_q"


def test_edit_element_tolerates_broken_document(window):
    """坏元素文件不该让 GUI 崩（读取失败就地报错）。"""
    window._save_named_flow("ed4")
    store = window._element_store()
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "broken.json").write_text("{not json", encoding="utf-8")
    window._edit_element("broken")
    assert "读取元素失败" in window.statusBar().currentMessage()


# ---- R2（M44 残留）：候选提升后树的勾选态必须立刻回读 ------------------------
def _path_document_with_candidates() -> dict:
    doc = _path_document()
    doc["selector"]["candidates"] = [
        {
            "kind": "css",
            "selector": "div.wrap > button.ok:nth-of-type(2)",
            "matchedCount": 2,
        },
        {"kind": "css", "selector": "button.ok", "matchedCount": 3},
    ]
    return doc


def test_path_tree_resyncs_checks_from_current_css(qapp):
    """树的勾选态始终按**当前**主 css 回读（M47.11 替代原「提升后回读」判据）。

    M44 残留 R2 的教训在候选 UI 移除后换了入口：勾选态不能只在建树时算一次，
    之后任何改 css 的动作都要让它跟着回读，否则中间态里「树显示的层级」与
    「主选择器实际是哪条」是两回事。原判据靠 ``_promote()`` 触发，现改为直接调
    ``_sync_path_checks_from_css()`` —— 判的是**回读本身**，而不是某个已删的入口。
    """
    from PySide6.QtCore import Qt

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document_with_candidates())
    assert form.css_edit.text() == "body > div.wrap > button.ok:nth-of-type(2)"
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    )

    # 截短祖先 → 勾选态与 css 一致（末两级）
    form.path_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert form.css_edit.text() == "div.wrap > button.ok:nth-of-type(2)"

    # css 改成路径的后缀 → 回读后维持「末两级」
    form.css_edit.setText("div.wrap > button.ok:nth-of-type(2)")
    form._sync_path_checks_from_css()
    assert form.path_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert form.path_list.item(1).checkState() == Qt.CheckState.Checked
    assert form.path_list.item(2).checkState() == Qt.CheckState.Checked

    # css **不**出自路径 → 回读后回退「完整路径视角」（全勾），而不是停在旧勾选
    form.css_edit.setText("button.ok")
    form._sync_path_checks_from_css()
    assert all(
        form.path_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(3)
    ), "回读不出就从完整路径视角来，而不是停在旧勾选"


# ---- Web 属性表（A1）：逐属性勾选 + 等于/包含编译回 fragment ------------------
def test_attribute_rows_initial_state_derived_from_fragment(qapp):
    """初态从**当前 fragment** 反推：捕获用了哪些属性就勾哪些。

    content.js 口径：带 id 的层 fragment 就是 `#id`（tag 不出现）；无 id 层只取
    **首类** + 同名兄弟时补 :nth-of-type —— 所以首类勾、次类不勾是「如实还原」，
    次类的勾选权正是属性表相对节点树新增的颗粒度。
    """
    from rpa_core.gui.element_editor import attribute_rows

    # 无 id 层：tag + 首类 + nth 都在 fragment 里
    entry = {
        "tag": "button", "id": None, "classes": ["ok", "btn-primary"],
        "nthOfType": 2, "fragment": "button.ok:nth-of-type(2)",
    }
    rows = attribute_rows(entry, "button.ok:nth-of-type(2)")
    assert [(r["attr"], r["value"], r["checked"], r["locked"]) for r in rows] == [
        ("tag", "button", True, True),
        ("class", "ok", True, False),
        ("class", "btn-primary", False, False),
        ("nth-of-type", "2", True, False),
    ]

    # 带 id 层：id 勾、类不勾（fragment 是 #id，tag 不参与）
    entry2 = {
        "tag": "div", "id": "main", "classes": ["a", "b"],
        "nthOfType": None, "fragment": "#main",
    }
    rows2 = attribute_rows(entry2, "#main")
    assert [(r["attr"], r["checked"]) for r in rows2] == [
        ("tag", True), ("id", True), ("class", False), ("class", False),
    ]


def test_compile_fragment_match_modes(qapp):
    from rpa_core.gui.element_editor import (
        ATTR_CONTAINS,
        ATTR_EQUALS,
        compile_fragment,
    )

    entry = {"tag": "div", "id": "main", "classes": ["wrap"], "nthOfType": 3}

    def rows(**kw):
        return [
            {"attr": "tag", "value": "div", "mode": None, "checked": True,
             "locked": True},
            {"attr": "id", "value": "main",
             "mode": kw.get("id_mode", ATTR_EQUALS),
             "checked": kw.get("id", False), "locked": False},
            {"attr": "class", "value": "wrap", "mode": kw.get("mode", ATTR_EQUALS),
             "checked": kw.get("cls", True), "locked": False},
            {"attr": "nth-of-type", "value": "3", "mode": None,
             "checked": kw.get("nth", True), "locked": False},
        ]

    # id 等于：整层 = #id（捕获口径：等值 id 本身唯一，其余属性不参与）
    assert compile_fragment(entry, rows(id=True)) == "#main"
    # id 勾「包含」：不能再用 #id 简写 → [id*=…]；class/nth 照常参与
    # （只有 id「等于」才整层接管——等值 id 本身唯一）
    assert compile_fragment(entry, rows(id=True, id_mode=ATTR_CONTAINS)) == (
        'div[id*="main"].wrap:nth-of-type(3)'
    )
    # 取消 class、勾 id 包含
    assert compile_fragment(
        entry, rows(cls=False, nth=False, id=True, id_mode=ATTR_CONTAINS)
    ) == 'div[id*="main"]'
    # class 等于 / 包含
    assert compile_fragment(entry, rows()) == "div.wrap:nth-of-type(3)"
    assert compile_fragment(entry, rows(mode=ATTR_CONTAINS)) == (
        'div[class*="wrap"]:nth-of-type(3)'
    )
    # 取消 nth
    assert compile_fragment(entry, rows(nth=False)) == "div.wrap"
    # 值含引号：包含编译必须转义（否则拼出非法选择器）
    quote_entry = {"tag": "div", "id": None, "classes": ['a"b'], "nthOfType": None}
    quote_rows = [
        {"attr": "tag", "value": "div", "mode": None, "checked": True,
         "locked": True},
        {"attr": "class", "value": 'a"b', "mode": ATTR_CONTAINS,
         "checked": True, "locked": False},
    ]
    assert compile_fragment(quote_entry, quote_rows) == 'div[class*="a\\"b"]'


def test_attr_table_rewrites_css_with_match_mode(qapp):
    """表单级：改匹配方式/勾选 → 该层 fragment 重写 → 主选择器跟着重写。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    # 默认选中末级（目标层）：button.ok:nth-of-type(2) → 行 = tag/class/nth
    assert form.path_list.currentRow() == 2
    assert form.attr_table.rowCount() == 3

    # class 行（第 1 行）改「包含」→ 该层变 [class*="ok"]，主选择器重写
    combo = form.attr_table.cellWidget(1, 2)
    assert combo is not None
    combo.setCurrentIndex(1)
    assert form.css_edit.text() == (
        'body > div.wrap > button[class*="ok"]:nth-of-type(2)'
    )
    assert not form.blocked

    # 取消 nth（第 2 行勾选框）→ fragment 去掉 :nth-of-type
    nth_box = form.attr_table.cellWidget(2, 0)
    nth_box.setChecked(False)
    assert form.css_edit.text() == 'body > div.wrap > button[class*="ok"]'

    # 树行文本与 fragment 同步（树的下一轮勾选拼回的也是新 fragment）
    assert form.path_list.item(2).text() == 'button[class*="ok"]'

    # tag 行的勾选框锁定（不勾 tag 的层不指向任何东西）
    assert not form.attr_table.cellWidget(0, 0).isEnabled()


def test_attr_table_follows_tree_selection(qapp):
    """属性表随树选中行切换：切到中间层（div.wrap）显示该层的属性。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    form.path_list.setCurrentRow(1)  # div.wrap：tag + class + nth
    assert form.attr_table.rowCount() == 3
    assert form.attr_table.item(1, 1).text() == "class"
    assert form.attr_table.item(1, 3).text() == "wrap"
    # 该层 class 改「包含」只影响中间层，末级不受牵连
    form.attr_table.cellWidget(1, 2).setCurrentIndex(1)
    assert form.css_edit.text() == (
        'body > div[class*="wrap"] > button.ok:nth-of-type(2)'
    )


# ---- M48：编辑中预览（通道回调注入式） ---------------------------------------

def _preview_stub_log():
    """可观察的预览/清场替身：记录调用，返回结构化结果（同通道形状）。"""
    log = {"preview": [], "clear": [], "result": {"count": 1}}

    def preview(css):
        log["preview"].append(css)
        return dict(log["result"])

    def clear():
        log["clear"].append(True)
        return {"count": 0}

    return log, preview, clear


def _drain_qt_events(qtbot_like_wait=0.05):
    """给工作线程留出 emit 窗口后泵一次事件循环（offscreen 无 app.processEvents 桩）。"""
    import time

    from PySide6.QtCore import QCoreApplication

    time.sleep(qtbot_like_wait)
    QCoreApplication.processEvents()


def test_form_live_preview_dispatch_and_label(qapp):
    """css 改动 → 防抖到期后在工作线程调 preview 回调，标签回显命中。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(preview, clear)
    assert not form.preview_label.isHidden()  # 注入即显示，桌面表单才整个没有

    form.css_edit.setText("#sb_form_q2")  # 触发 textChanged → 防抖重启
    assert form._preview_timer.isActive()  # 只排程，未到期不打通道
    assert log["preview"] == []

    form._preview_timer.stop()
    form._run_preview()  # 直接驱动到期分支（不等真实 500ms）
    _drain_qt_events()
    assert log["preview"] == ["#sb_form_q2"]
    assert "命中 1 个" in form.preview_label.text()
    assert log["clear"] == []  # 正常预览不清场
    # ③ 文案去重：不再有「预览：」前缀（命中数那条已由场景体现，标签不必自称「预览」）
    assert "预览：" not in form.preview_label.text()


def test_form_preview_empty_css_clears_instead_of_querying(qapp):
    """css 清空：不发查找、就地清场——旧框不能赖着冒充新选择器的命中。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(preview, clear)

    form.css_edit.setText("   ")
    form._preview_timer.stop()
    form._run_preview()
    _drain_qt_events()
    assert log["preview"] == []
    assert log["clear"] == [True]
    assert form.preview_label.text() == ""


def test_form_preview_error_shows_inline(qapp):
    """通道故障落到预览标签（结构化 error 与异常都要可见，不静默）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    def boom(_css):
        raise RuntimeError("channel down")

    log, _preview, clear = _preview_stub_log()
    form = ElementEditorForm(_browser_document())
    form.enable_live_preview(boom, clear)
    form._preview_timer.stop()
    form._run_preview()
    _drain_qt_events()
    assert "预览失败" in form.preview_label.text()
    assert "channel down" in form.preview_label.text()


def test_form_stale_preview_result_is_discarded(qapp):
    """防抖后再来一轮时，上一轮的迟到结果不得覆盖显示（seq 配对）。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_browser_document())
    log, preview, clear = _preview_stub_log()
    form.enable_live_preview(preview, clear)
    form._preview_timer.stop()
    form._run_preview()  # seq=1 在途
    form._run_preview()  # seq=2：模拟用户又改了一轮
    form._on_preview_done({"kind": "preview", "seq": 1, "count": 7})
    assert "预览中…" in form.preview_label.text() or "7" not in form.preview_label.text()
    form._on_preview_done({"kind": "preview", "seq": 2, "count": 7})
    assert "命中 7 个" in form.preview_label.text()


def test_desktop_form_has_no_preview_surface(qapp):
    """桌面元素没有「页面」可高亮：enable_live_preview 必须整体无效。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    log, preview, clear = _preview_stub_log()
    form = ElementEditorForm(_desktop_document())
    form.enable_live_preview(preview, clear)
    assert not hasattr(form, "preview_label") or form.preview_label.isHidden()
    form._run_preview() if hasattr(form, "_preview_timer") else None
    _drain_qt_events()
    assert log["preview"] == [] and log["clear"] == []


def test_dialog_close_sends_clear_preview(qapp):
    """关窗收场：ElementEditorDialog finished → shutdown_preview 发 clear。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    log, preview, clear = _preview_stub_log()
    dialog = ElementEditorDialog(
        _browser_document(), name="el_x", preview_css=preview, clear_preview_css=clear
    )
    dialog.form._preview_timer.stop()
    dialog.finished.emit(0)  # 不 exec（模态会挂测试），直接驱动 finished 接线
    _drain_qt_events()
    assert log["clear"] == [True]


def test_dialog_without_preview_callback_has_no_preview(qapp):
    """未注入回调（如 desktop / 测试注入 None）：不启用预览、关窗也不发 clear。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    log, preview, clear = _preview_stub_log()
    dialog = ElementEditorDialog(_browser_document(), name="el_y")
    dialog.form._preview_timer.stop()
    dialog.finished.emit(0)
    _drain_qt_events()
    assert log["preview"] == [] and log["clear"] == []


# ---- M47.3：编辑区空间放开（维护者实测「列表空间小，不方便查看选择」） ----------

def test_attr_table_and_path_list_have_room(qapp):
    """属性表不得再有 160px 上限（只装 3~4 行）；表格与节点树给足最小高度，
    且表格随对话框拉伸（Expanding）——列表是编辑区主工作区。"""
    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())  # 有 path 才会建节点树与属性表
    assert form.attr_table.minimumHeight() >= 220
    assert form.attr_table.maximumHeight() >= 2000  # 无 160px 类上限
    assert (
        form.attr_table.sizePolicy().verticalPolicy()
        == __import__("PySide6.QtWidgets", fromlist=["QSizePolicy"]).QSizePolicy.Policy.Expanding
    )
    assert form.path_list.minimumHeight() >= 140


def test_editor_dialog_opens_roomy(qapp):
    """编辑对话框默认尺寸要配得上主工作区（680x720 起步），窄窗压表是旧账。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_browser_document(), name="el_size")
    assert dialog.width() >= 680
    assert dialog.height() >= 700


# ---- M47.10：捕获确认框三处体验（维护者实测反馈） ---------------------------

def test_path_tree_and_attr_table_are_side_by_side(qapp):
    """① 节点树与属性表**并排**（QSplitter 横向：左树、右表），不再上下堆叠。

    判据用**父子关系**而不是「有没有这个控件」：摆一个 QSplitter 但把树和表塞进
    同一个 QVBoxLayout，视觉上仍是上下堆叠——只有在同一个横向 splitter 里才算并排。

    **均分**（M47.12）由 :func:`test_path_tree_and_attr_table_are_equal_width` 单独钉住。
    """
    from PySide6.QtWidgets import QSplitter

    from rpa_core.gui.element_editor import ElementEditorForm

    form = ElementEditorForm(_path_document())
    splitter = form.path_splitter
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == __import__(
        "PySide6.QtCore", fromlist=["Qt"]
    ).Qt.Orientation.Horizontal
    # 树在左、表在右（index 0 / 1），两者同属这一个 splitter
    assert splitter.widget(0) is form.path_list
    assert splitter.widget(1) is form.attr_table


def test_selector_edit_is_multiline_and_grows(qapp):
    """④ 主选择器输入框是**多行**（QPlainTextEdit），长祖先链不再被截掉尾巴。

    ``QLineEdit`` 一行装不下祖先链、尾部 CSS 被截断；换成多行后整段可读，
    高度按内容自适应（1~4 行）。对外仍保留 ``.text()`` / ``.setText()`` 同名 API。
    """
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

    from rpa_core.gui.element_editor import ElementEditorForm, SelectorEdit

    form = ElementEditorForm(_browser_document())
    assert isinstance(form.css_edit, SelectorEdit)
    assert isinstance(form.css_edit, QPlainTextEdit)
    assert not isinstance(form.css_edit, QLineEdit)
    # 同名 API 保住：调用点（二十来处）不需要跟着改
    assert form.css_edit.text() == form.css_edit.toPlainText()
    form.css_edit.setText("a")
    assert form.css_edit.text() == "a"
    # 横向滚动条关掉（换行即整段可见）；纵向按需
    assert form.css_edit.horizontalScrollBarPolicy() == __import__(
        "PySide6.QtCore", fromlist=["Qt"]
    ).Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_selector_edit_settext_emits_textchanged(qapp):
    """**信号遮蔽的钉子**：``setText`` / ``setPlainText`` 必须触发 ``textChanged``。

    初版把 ``textChanged = Signal(str)`` 盖在 ``QPlainTextEdit`` 原生信号上，结果
    ``super().textChanged`` 在 MRO 下仍解析到子类那个从未 emit 的信号，
    ``setText`` 之后界面毫无反应（``test_editor_browser_blocks_blank_css`` 直接红）。
    这条判据钉住修复：不用看下游是否变色，只证明信号**真的**发出来了。
    """
    from rpa_core.gui.element_editor import SelectorEdit

    edit = SelectorEdit("a")
    fired: list[bool] = []
    edit.textChanged.connect(lambda: fired.append(True))
    edit.setText("   ")
    assert fired == [True]
    edit.setPlainText("yy")
    assert fired == [True, True]


def test_selector_edit_height_tracks_line_count(qapp):
    """高度自适应：多行内容比单行高，且封顶在 4 行（不无限膨胀把对话框撑爆）。"""
    from rpa_core.gui.element_editor import SelectorEdit

    single = SelectorEdit("a")
    many = SelectorEdit("\n".join(f"div.layer{i}" for i in range(20)))
    assert many.height() > single.height()
    # 封顶：20 行内容不会换来 20 行高度（上限 4 行 + 边距）
    assert many.height() < single.height() * 6


# ---- M48：桌面 locator 结构字段（path / anchor / matchMode）不得在编辑中丢失 ------

def _desktop_document_with_extensions() -> dict:
    """带 M48 三件套的桌面元素：祖先链 + 锚点 + 控件级 matchMode。"""
    return {
        "kind": "desktop",
        "selector": {
            "locator": {
                "backend": "uia",
                "controlType": "Button",
                "automationId": "submitButton",
                "name": "Submit",
                "matchMode": "contains",
                "path": [
                    {"controlType": "Window", "name": "RPA Core Desktop Demo"},
                    {"controlType": "Pane", "automationId": "formPanel"},
                ],
                "anchor": {"locator": {"backend": "uia", "automationId": "mainWindow"}},
            }
        },
        "verifyCount": 1,
        "metadata": {"windowTitle": "RPA Core Desktop Demo"},
    }


def test_editor_roundtrip_preserves_structured_locator_fields(qapp):
    """**静默数据丢失的钉子**：编辑器打开再确定，path / anchor 必须原样还在。

    `compose_locator` 是「字符串值 → locator」的管道，结构字段不走它；若组装时忘了
    透传，用户在编辑器里点一次确定，捕获回传的祖先链与锚点就没了——而且**不报错**。
    """
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    out = dialog.result_document()
    locator = out["selector"]["locator"]
    assert locator["path"] == [
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
        {"controlType": "Pane", "automationId": "formPanel"},
    ]
    assert locator["anchor"] == {"locator": {"backend": "uia", "automationId": "mainWindow"}}
    assert locator["matchMode"] == "contains"


def test_editor_does_not_flag_structured_fields_as_inert(qapp):
    """path / anchor / matchMode 被执行器真读，不能报成「死字段」。"""
    from rpa_core.gui.element_editor import inert_locator_keys

    locator = _desktop_document_with_extensions()["selector"]["locator"]
    assert inert_locator_keys(locator) == []


def test_editor_renders_locator_path_tree(qapp):
    """祖先链表格逐级一行，**空 path 也建表**（M50 起可编辑，用户要能新建祖先链）。

    M48 是「空 ⇒ 不建控件」，M50 改为常驻：没有入口用户就无从给旧元素补祖先链。
    「空 path 不产出 `path` 字段」这条不变量挪到 **结果文档**上验证（见下一条断言）。
    """
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    table = dialog.path_table
    assert table.rowCount() == 2
    assert table.item(0, 0).text() == "Window"
    assert table.item(1, 1).text() == "formPanel"
    # 空 path：表格仍存在（可新建），但结果文档里没有 path 字段
    plain = ElementEditorDialog(_desktop_document(), name="plain")
    assert plain.path_table.rowCount() == 0
    assert "path" not in plain.result_document()["selector"]["locator"]


def test_editor_compose_with_match_mode_only(qapp):
    """matchMode 是标量字段：可直接在输入框里改，且组装进 locator。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    dialog.field_edits["matchMode"].setText("regex")
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["matchMode"] == "regex"


# ---- M50：祖先链（path）与锚点（anchor）**可编辑** ----------------------------
#
# M48 给了「渲染出来」的只读视图，M50 把它升级成可改写入口。这段判据盯的是
# **两个方向**：改动能落进结果文档（写侧）、以及编辑器与捕获侧对「一级长什么样」
# 的口径必须**同源**（两端同规则，防漂移）。


def _set_path_cell(dialog, row: int, key: str, text: str) -> None:
    """改表格里某一格（走控件真实通道：setText 会触发 itemChanged）。"""
    from PySide6.QtWidgets import QTableWidgetItem

    from rpa_core.model.desktop import LOCATOR_STEP_KEYS

    col = LOCATOR_STEP_KEYS.index(key)
    item = dialog.path_table.item(row, col)
    if item is None:
        dialog.path_table.setItem(row, col, QTableWidgetItem(text))
    else:
        item.setText(text)


def test_editor_can_edit_path_step_keys(qapp):
    """改级内键：在表格里改一格，结果文档的 path 随之改变（M48 是只读）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    _set_path_cell(dialog, 1, "automationId", "renamedPanel")
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["path"] == [
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
        {"controlType": "Pane", "automationId": "renamedPanel"},
    ]


def test_editor_can_add_and_remove_path_levels(qapp):
    """增删级：加一级 → path 多一级；删掉它 → 回到原样（且不残留空级）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    dialog.path_table.setCurrentCell(0, 0)
    dialog._on_path_add_level()
    # 加的一级是空的 ⇒ 被共享纯函数丢弃，结果文档不会多出空级
    assert len(dialog.result_document()["selector"]["locator"]["path"]) == 2
    _set_path_cell(dialog, 1, "name", "middlePane")
    assert dialog.result_document()["selector"]["locator"]["path"] == [
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
        {"name": "middlePane"},
        {"controlType": "Pane", "automationId": "formPanel"},
    ]
    dialog.path_table.setCurrentCell(1, 0)
    dialog._on_path_remove_level()
    assert dialog.result_document()["selector"]["locator"]["path"] == [
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
        {"controlType": "Pane", "automationId": "formPanel"},
    ]


def test_editor_path_move_reorders_levels(qapp):
    """上移/下移：祖先链**有序**，顺序错了收窄会走错分支——移动必须真落到结果文档。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    dialog.path_table.setCurrentCell(1, 0)
    dialog._on_path_move(-1)  # Pane 上移到 Window 之前
    assert dialog.result_document()["selector"]["locator"]["path"] == [
        {"controlType": "Pane", "automationId": "formPanel"},
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
    ]
    dialog.path_table.setCurrentCell(0, 0)
    dialog._on_path_move(1)  # 移回去
    assert dialog.result_document()["selector"]["locator"]["path"] == [
        {"controlType": "Window", "name": "RPA Core Desktop Demo"},
        {"controlType": "Pane", "automationId": "formPanel"},
    ]


def test_editor_path_empty_level_is_dropped_and_hinted(qapp):
    """全空级丢弃 + 给用户提示（每级至少要一个键，模型会拒；界面不能静默吞掉）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    plain = ElementEditorDialog(_desktop_document(), name="plain")
    plain._on_path_add_level()  # 造出一整行空级
    assert "path" not in plain.result_document()["selector"]["locator"]
    assert "丢弃" in plain.path_hint.text()


def test_editor_path_emptying_last_level_removes_field(qapp):
    """把带 path 的元素逐级清空 ⇒ path 字段整体移除（等价「不收窄」），不留空列表。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    for key in ("controlType", "name"):
        _set_path_cell(dialog, 0, key, "")
    for key in ("controlType", "automationId"):
        _set_path_cell(dialog, 1, key, "")
    assert "path" not in dialog.result_document()["selector"]["locator"]


def test_editor_path_edit_is_idempotent_when_untouched(qapp):
    """**编辑不改动 ⇒ 逐字节原样**（M48 修过一次静默丢数据，不能退回去）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    document = _desktop_document_with_extensions()
    dialog = ElementEditorDialog(document, name="submit")
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["path"] == document["selector"]["locator"]["path"]
    assert locator["anchor"] == document["selector"]["locator"]["anchor"]


def test_editor_path_pruning_is_shared_with_capture(qapp):
    """**两端同规则**：编辑器重拼与捕获侧收级共用 ``prune_locator_steps``（防两处漂移）。"""
    from rpa_core.capture.desktop_agent import _locator_step_for
    from rpa_core.model.desktop import prune_locator_steps

    class _Info:
        control_type = "ComboBox"
        automation_id = ""
        name = "  "
        class_name = None

    # 捕获侧：只有 controlType 有值 ⇒ 保留该键、其余丢弃
    assert _locator_step_for(_Info()) == {"controlType": "ComboBox"}
    # 编辑器侧：同一份草稿喂给共享纯函数，得到逐字节相同的一级
    assert prune_locator_steps([_locator_step_for(_Info())]) == [{"controlType": "ComboBox"}]

    class _Empty:
        control_type = None
        automation_id = None
        name = ""
        class_name = None

    # 一个键都没有 ⇒ 捕获侧跳过该级（None），编辑器侧丢弃该级——**同一口径**
    assert _locator_step_for(_Empty()) is None
    assert prune_locator_steps([{}]) == []


def test_editor_anchor_can_be_enabled_and_edited(qapp):
    """锚点一片：勾上 + 填字段 ⇒ 结果文档里 anchor = {"locator": {...}}。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    assert "anchor" not in dialog.result_document()["selector"]["locator"]
    dialog.anchor_box.setChecked(True)
    dialog.anchor_edits["automationId"].setText("mainWindow")
    locator = dialog.result_document()["selector"]["locator"]
    assert locator["anchor"] == {"locator": {"backend": "uia", "automationId": "mainWindow"}}


def test_editor_anchor_uncheck_removes_it(qapp):
    """取消勾选 ⇒ anchor 整个移除（回到「无锚点」），而不是留一个空 shell。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document_with_extensions(), name="submit")
    assert "anchor" in dialog.result_document()["selector"]["locator"]
    dialog.anchor_box.setChecked(False)
    assert "anchor" not in dialog.result_document()["selector"]["locator"]


def test_editor_anchor_form_has_no_nested_anchor_entry(qapp):
    """**只给一层**：锚点表单里没有「再加锚点」的入口，用户天然构造不出嵌套。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_desktop_document(), name="submit")
    assert dialog.anchor_edits
    assert not hasattr(dialog, "anchor_anchor_box")
    assert not hasattr(dialog, "anchor_path_table")


def test_model_rejects_nested_anchor():
    """界面只给一层，但**模型层才是判据权威**：真构造一条嵌套的，校验必报。"""
    from pydantic import ValidationError

    from rpa_core.model.desktop import DesktopLocator

    payload = {
        "backend": "uia",
        "automationId": "target",
        "anchor": {
            "locator": {
                "backend": "uia",
                "automationId": "outer",
                "anchor": {"locator": {"backend": "uia", "automationId": "inner"}},
            }
        },
    }
    with pytest.raises(ValidationError) as excinfo:
        DesktopLocator.model_validate(payload)
    assert "anchor" in str(excinfo.value)


def test_browser_preview_passes_stale_reason_through_to_gui(qapp):
    """``_browser_preview`` 必须把「扩展旧快照」这一档**真的送到 GUI**。

    这是链路上的一环，此前无人钉：纯函数 :func:`shot_failure_reason` 钉的是「怎么分类」，
    GUI 判据钉的是「拿到 ext-stale 怎么写文案」，而中间这步（旧扩展 → 旧文案链）
    两边都够不着。真机现场正是这里断掉的表现：预览空白 + 一句误导性的
    「未能定位到浏览器窗口」，用户无从判断该去切窗口还是该去重载扩展。
    """
    from rpa_core.capture import verify as verify_mod
    from rpa_core.gui.app import _browser_preview

    class _Verifier:
        def verify(self, _css, *, want_shot=False):
            assert want_shot is True
            #旧扩展：没有 windowHandle，但自报了一个对不上的 extBuild
            return {"count": 1, "extBuild": "0.6.3"}

    monkey = getattr(verify_mod, "browser_preview_shot", None)
    out = _browser_preview(_Verifier(), "#kw")
    assert out["count"] == 1
    assert out["shotError"] == "ext-stale"
    assert out["extBuild"] == "0.6.3"
    del monkey
