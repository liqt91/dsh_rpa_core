"""GUI 画布中文化契约测试（M56）：卡片标题与参数摘要都不再裸露英文。

覆盖三层：

1. **标题**：画布 action 卡片标题 = 中文显示名（与 Web ``app.js`` 的
   ``commandName`` 同口径）；未知命令回退 id 原文。
2. **摘要**：卡片右侧参数摘要的**键名**走 ``i18n.field_label``
   （命令级覆盖 → 公共键 → 键名原文）——否则卡片上仍是 ``selector=#kw``。
3. ★ **产出路径一致**：装载 / 新增 / 粘贴三条路径产出的标题**逐字节相等**，
   摘要也相等。只改一条会得到「有的卡片中文、有的还是英文」——M52 S3 踩过
   同款（判据覆盖了实现、却没覆盖用户实际走的那条路）。

另有两条防漂移闸：

- ``app.py`` 里每一处 ``summarize_args`` 调用都必须喂 ``command_id``。参数应用后
  摘要会**重算**，漏喂即「改完参数、摘要退回裸键名」——纯函数对、调用方没用上。
- 画布搜索文本仍含英文 command id：标题改中文后，英文 id 必须**仍搜得到**。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import ast
import os

# 必须在导入 Qt / 创建 QApplication 之前指定离屏平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_SUMMARY,
    ROLE_COMMAND_ID,
    FlowTreeModel,
    build_model_from_workflow,
    command_label,
    summarize_args,
)
from rpa_core.gui.i18n import (  # noqa: E402
    field_label,
    load_command_display_names,
    load_field_labels,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_PY = REPO_ROOT / "src" / "rpa_core" / "gui" / "app.py"

DISPLAY = Qt.ItemDataRole.DisplayRole


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    return load_catalog(_commands_root())


def _top_level(model: FlowTreeModel) -> list:
    root = model.invisibleRootItem()
    return [root.child(row) for row in range(root.rowCount())]


def _sequence(*nodes: dict) -> dict:
    return {"root": {"type": "sequence", "id": "root", "children": [dict(n) for n in nodes]}}


def _action(node_id: str, command_id: str, args: dict | None = None) -> dict:
    return {"type": "action", "id": node_id, "command": command_id, "with": args or {}}


# ---- 1. 标题中文化 ---------------------------------------------------------
def test_canvas_action_title_is_chinese(catalog):
    """画布卡片标题是中文显示名，不再是 ``browser.navigate`` 这种裸 id。"""
    commands = ["browser.navigate", "browser.input", "data.setVar"]
    for command_id in commands:
        assert command_id in catalog, f"夹具命令 {command_id} 不在 catalog 里"
    model = build_model_from_workflow(
        _sequence(*[_action(f"n{i}", cid) for i, cid in enumerate(commands, start=1)])
    )
    names = load_command_display_names()
    shown = [item.data(DISPLAY) for item in _top_level(model)]
    assert shown == [names[cid] for cid in commands], shown
    # 反向：裸 id 不该再出现在任何标题里（这就是「画布还是英文」的字面形态）
    for command_id in commands:
        assert command_id not in shown
        assert command_id not in names[command_id], f"{command_id} 的中文名里居然含 id 原文"


def test_canvas_title_falls_back_to_raw_id(catalog):
    """未知命令（不在 i18n 表里）回退 id 原文——不假装有中文。"""
    unknown = "no.such.command"
    assert unknown not in load_command_display_names()
    assert command_label(unknown) == unknown
    model = build_model_from_workflow(_sequence(_action("n1", unknown)))
    assert _top_level(model)[0].data(DISPLAY) == unknown


# ---- 2. 参数摘要键名中文化 --------------------------------------------------
def test_canvas_summary_keys_use_chinese_labels(catalog):
    """卡片摘要的键名是中文标签（含 M54 的元素槽位口径「操作目标」）。"""
    command_id = "browser.input"
    args = {"selector": "#kw", "text": "hello"}
    model = build_model_from_workflow(_sequence(_action("n1", command_id, args)))
    summary = _top_level(model)[0].data(ROLE_ARGS_SUMMARY) or ""
    keys = [part.split("=", 1)[0] for part in summary.split("  ") if "=" in part]
    assert keys, summary
    assert set(keys) == {field_label(key, command_id) for key in args}, summary
    # 裸 schema 键名不该再出现在摘要里
    assert "selector=" not in summary
    assert "操作目标=#kw" in summary


def test_summary_prefers_command_level_override():
    """同名参数在不同命令下含义不同 ⇒ 摘要也必须走命令级覆盖。

    ``data.setVar`` 的 ``varName`` 是「变量名」，公共键 fallback 是「接收变量名」；
    两者必须能区分，否则说明 ``commandFields`` 那一层没被摘要用上。
    """
    assert load_field_labels()["varName"] == "接收变量名"
    assert summarize_args({"varName": "总数"}, command_id="data.setVar") == "变量名=总数"
    assert summarize_args({"varName": "总数"}) == "接收变量名=总数"


# ---- 3. ★ 三条产出路径必须一致 ---------------------------------------------
def test_all_production_paths_agree_on_title_and_summary(catalog):
    """装载 / 新增 / 粘贴三条路径产出的标题与摘要**逐字节相等**。

    「两条路各自绿」抓不到「各自漂移」，而事故形状恰恰是「一条有、一条没有」。
    """
    command_id = "browser.input"
    args = {"selector": "#kw", "text": "hello"}

    # 三条路径的模型都必须**持有引用**：QStandardItem 的所有权在模型上，模型被 GC
    # ⇒ item 被原生销毁，之后任何读写都抛「Internal C++ object already deleted」。

    # 路径 A：装载 workflow（MainWindow.set_workflow → build_model_from_workflow）
    model_a = build_model_from_workflow(_sequence(_action("n1", command_id, args)))
    loaded = _top_level(model_a)[0]

    # 路径 B：从指令树新增 / Ctrl+P 插入（MainWindow.add_command → model.insert_command）
    model_b = FlowTreeModel()
    inserted = model_b.insert_command(command_id)

    # 路径 C：粘贴（MainWindow 粘贴 → model.insert_subtree）
    model_c = FlowTreeModel()
    pasted = model_c.insert_subtree(_action("n1", command_id, args))

    # 标题：三条路径逐字节相等，且都等于 i18n 的中文名
    titles = {loaded.data(DISPLAY), inserted.data(DISPLAY), pasted.data(DISPLAY)}
    assert titles == {load_command_display_names()[command_id]}, titles

    # 摘要：A/C 带同样的参数 ⇒ 逐字节相等；B 是新建空节点 ⇒ 键名同样中文化后为空。
    # ★ 这里额外给一个**字面独立证人**：只用 summarize_args 算期望值的话，
    #   期望与实际**同源**（都经同一个纯函数）——纯函数整体失效时两边一起退化成
    #   裸键名、断言恒真（M54 探针抓到过同款假绿灯）。字面串是不可伪造的那一层。
    expected = summarize_args(args, command_id=command_id)
    assert expected == "操作目标=#kw  文本内容=hello", expected
    assert loaded.data(ROLE_ARGS_SUMMARY) == expected
    assert pasted.data(ROLE_ARGS_SUMMARY) == expected
    assert inserted.data(ROLE_ARGS_SUMMARY) == summarize_args({}, command_id=command_id) == ""
    assert loaded.data(ROLE_COMMAND_ID) == pasted.data(ROLE_COMMAND_ID) == command_id


# ---- 防漂移闸 --------------------------------------------------------------
def test_app_every_summarize_args_call_passes_command_id():
    """``app.py`` 里每一处 ``summarize_args`` 调用都必须喂 ``command_id``。

    参数应用/元素引用后摘要会**重算**；漏喂就得到「改完参数、摘要退回裸键名」。
    纯函数正确 ≠ 调用方用上了（M52 的教训）。
    """
    tree = ast.parse(_APP_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        == "summarize_args"
    ]
    assert calls, "app.py 里找不到 summarize_args 调用——判据失锚（函数被改名/搬走了？）"
    missing = [
        call.lineno
        for call in calls
        if not any(kw.arg == "command_id" for kw in call.keywords)
    ]
    assert not missing, f"这些 summarize_args 调用没喂 command_id：行 {missing}"


def test_canvas_search_text_still_matches_english_id(catalog):
    """标题改中文后，英文 command id 必须**仍能被画布搜索命中**。

    搜索文本含 ``ROLE_COMMAND_ID``（``MainWindow._canvas_item_search_text``）——
    这条钉住它，防止有人「顺手」把它删掉，让老用户按 id 搜不到。
    """
    from rpa_core.gui.app import MainWindow

    command_id = "browser.navigate"
    model = build_model_from_workflow(_sequence(_action("n1", command_id, {"url": "u"})))
    text = MainWindow._canvas_item_search_text(_top_level(model)[0])
    assert command_id in text, text
    # 中文名同样可搜（新增能力，不替代英文）
    assert load_command_display_names()[command_id] in text
