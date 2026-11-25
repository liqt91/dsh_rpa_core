"""输出变量改名的联动契约（M108）：改一个别名，全流程指向它的引用一起改。

为什么值得一个独立判据文件：改名是**唯一会动别的节点**的保存动作。参数保存改的是
同一个节点里的一格，改名改的是「所有引用过这个名字的地方」，而其中一个漏掉就是
「保存成功 + 编译红」（或更糟：编译过、运行期取到旧值）。所以这里的判据不是
「函数算得对」，而是「**每一类引用站点**都真的被走了」：

* action 的 `with`（普通 `${...}` 与 fx `[...]` 两种模式）；
* python 模式字段里的裸标识符（按 tokenize 的 NAME token，不碰字符串与注释）；
* if 的 `condition.left/right`、forEach 的 `items`、return 的 `value`。

这四个站点是 `model/workflow.py` 里**全部**吃表达式的字段。将来新增吃表达式的字段
时，这里会缺一格——所以每个站点各有一条判据，缺哪格红哪格。

辅助函数与本目录的 `test_editor_param_form.py` 各有一份：两个文件验的是不同的契约，
共享夹具会让「改一处、两边都动」——那份夹具本身就是判据的一部分。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "editor" / "api" / "api.py"


def _load_api():
    """加载 `apps/editor/api/api.py`：`ppx_py` 打桩（门禁环境没有 ppx 运行时）。"""
    stub = types.ModuleType("ppx_py")

    class BridgeError(Exception):
        def __init__(self, code: str, message: str):
            super().__init__(message)
            self.code = code

    stub.api_method = lambda _name: (lambda fn: fn)
    stub.BridgeError = BridgeError
    sys.modules["ppx_py"] = stub
    spec = importlib.util.spec_from_file_location("editor_api_rename", API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def editor(tmp_path, monkeypatch):
    api = _load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    monkeypatch.setattr(api, "SANDBOX", tmp_path / "_sandbox")
    api._flows.clear()
    return api


REL = "workflows/probe/rename.json"


def _write(api, nodes, inputs=None) -> str:
    target = api.WORKSPACE / REL
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": "1.0",
        "id": "rename.refs",
        "name": "rename refs",
        "inputs": inputs or {},
        "root": {"type": "sequence", "id": "root", "children": nodes},
    }
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 每次重写都是新文件：丢掉 `_flows` 里的旧 SHA，否则保存会被 FLOW_CHANGED 拦下。
    api._flows.pop(REL, None)
    return REL


def _target(api) -> Path:
    return api.WORKSPACE / REL


def _document(api) -> dict:
    return json.loads(_target(api).read_text(encoding="utf-8"))


def _find(document: dict, node_id: str) -> dict:
    """按 id 找节点（含嵌套分支），不假设位置。"""
    found: list[dict] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("id") == node_id:
            found.append(node)
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                walk(child)

    walk(document.get("root") or {})
    assert found, f"节点 {node_id} 不在流程里"
    return found[0]


def _declaring_node(node_id="dt1", alias="old", **extra) -> dict:
    """一个产出 `value/iso/timestamp` 的节点，并把 `value` 起名为 `alias`。

    选 `data.datetimeNow` 是因为它**不带资源依赖、没有必填参数**：编译能不能过与
    浏览器/桌面无关，判据红了就只可能是改名本身。
    """
    node = {
        "type": "action",
        "id": node_id,
        "command": "data.datetimeNow",
        "with": {"format": "iso"},
        "output_aliases": {"value": alias},
    }
    node.update(extra)
    return node


def _rename(api, rel: str, node_id: str, **aliases) -> dict:
    """提交一次全量别名（`value` 之外的两项留空 = 不设别名）。"""
    payload = {"value": aliases.get("value", ""), "iso": "", "timestamp": ""}
    payload.update({k: v for k, v in aliases.items() if k != "value"})
    return api.save_node_aliases(rel, node_id, payload)


def _set_var(node_id="set1", value="x", modes=None) -> dict:
    node = {
        "type": "action",
        "id": node_id,
        "command": "data.setVar",
        "with": {"varName": "msg", "value": value},
    }
    if modes:
        node["_exprModes"] = modes
    return node


# ---- 站点一：普通引用 -------------------------------------------------------
def test_rename_rewrites_plain_and_sub_path_references(editor):
    rel = _write(editor, [_declaring_node(), _set_var(value="${old}"), _log("${old.id}")])
    receipt = _rename(editor, rel, "dt1", value="new")

    assert receipt["ok"], receipt
    assert receipt["renamed"] == {"old": "new"}
    document = _document(editor)
    assert _find(document, "set1")["with"]["value"] == "${new}"
    # 子路径只换根名：`old.id` → `new.id`，`.id` 这段是取值路径，不能一起改
    assert _find(document, "log1")["with"]["message"] == "${new.id}"
    assert sum(hit["count"] for hit in receipt["refs"]) == 2
    assert receipt["compile"]["ok"], receipt["compile"]


def _log(message: str, node_id="log1") -> dict:
    return {"type": "action", "id": node_id, "command": "data.log", "with": {"message": message}}


# ---- 站点二：fx 标签 -------------------------------------------------------
def test_rename_rewrites_fx_tags_only_in_fx_fields(editor):
    """fx 字段里的 `[old]` 要改；普通字段里同样的文本是**字面文本**，一个都不许动。"""
    rel = _write(
        editor,
        [
            _declaring_node(),
            _set_var(value="前[old]后", modes={"value": "fx"}),
            _log("前[old]后"),
        ],
    )
    receipt = _rename(editor, rel, "dt1", value="new")

    assert receipt["ok"], receipt
    document = _document(editor)
    assert _find(document, "set1")["with"]["value"] == "前[new]后"
    assert _find(document, "log1")["with"]["message"] == "前[old]后", (
        "普通模式字段里的方括号是字面文本，改名把它一起改掉就是篡改用户数据"
    )
    assert sum(hit["count"] for hit in receipt["refs"]) == 1


# ---- 站点三：python 标识符 -------------------------------------------------
def test_rename_rewrites_python_identifiers_but_not_string_literals(editor):
    rel = _write(
        editor,
        [
            _declaring_node(),
            _set_var(value="old + 'old'  # old", modes={"value": "python"}),
        ],
    )
    receipt = _rename(editor, rel, "dt1", value="new")

    assert receipt["ok"], receipt
    code = _find(_document(editor), "set1")["with"]["value"]
    # 标识符改；字符串字面量与注释不碰（正则改法会把它们一起改掉）
    assert code == "new + 'old'  # old", code


def test_python_field_with_syntax_error_is_reported_not_silently_misnamed(editor):
    """py 源码读不出 token 时不猜：改名照做，但要在回执里点名这一格没改。"""
    rel = _write(
        editor,
        [
            _declaring_node(),
            _set_var(value="def (:", modes={"value": "python"}),
            _log("${old}"),
        ],
    )
    receipt = _rename(editor, rel, "dt1", value="new")

    assert receipt["ok"], receipt
    assert _find(_document(editor), "set1")["with"]["value"] == "def (:", "语法错的 py 源码被改了"
    assert receipt["pySkipped"] == [{"nodeId": "set1", "field": "value"}], receipt["pySkipped"]
    # 结构化引用照旧要改：不能因为有个改不动的 py 字段就整单放弃
    assert _find(_document(editor), "log1")["with"]["message"] == "${new}"


# ---- 站点四：条件 / 循环集合 / 返回值 ---------------------------------------
def test_rename_rewrites_condition_items_and_return(editor):
    rel = _write(
        editor,
        [
            _declaring_node(),
            {
                "type": "if",
                "id": "if1",
                "condition": {"op": "eq", "left": "${old}", "right": "${old.iso}"},
                "then": [
                    {
                        "type": "forEach",
                        "id": "fe1",
                        "items": "${old}",
                        "item_var": "row",
                        "children": [_log("[row]${old}")],
                    }
                ],
                "else": [],
            },
            {"type": "return", "id": "ret1", "value": "${old}"},
        ],
    )
    receipt = _rename(editor, rel, "dt1", value="new")

    assert receipt["ok"], receipt
    document = _document(editor)
    condition = _find(document, "if1")["condition"]
    assert condition["left"] == "${new}"
    assert condition["right"] == "${new.iso}"
    assert _find(document, "fe1")["items"] == "${new}"
    assert _find(document, "ret1")["value"] == "${new}"
    hit_fields = {(hit["nodeId"], hit["field"]) for hit in receipt["refs"]}
    assert hit_fields == {
        ("if1", "condition.left"),
        ("if1", "condition.right"),
        ("fe1", "items"),
        ("ret1", "value"),
        # 循环体里的日志节点也引用了旧名——它同样是「站点」，漏了就是漏改
        ("log1", "message"),
    }, hit_fields
    assert _find(document, "log1")["with"]["message"] == "[row]${new}"


# ---- 边界：不该动的时候一个字节都不动 ---------------------------------------
def test_alias_write_without_rename_does_not_touch_references(editor):
    """改名只认「同一输出字段的旧名 → 新名」。首次设别名不是改名，引用无从谈起。"""
    rel = _write(editor, [_declaring_node(alias=""), _log("${old}")])
    receipt = _rename(editor, rel, "dt1", value="old")

    assert receipt["ok"], receipt
    assert receipt["renamed"] == {}
    assert receipt["refs"] == []
    assert _find(_document(editor), "log1")["with"]["message"] == "${old}"


def test_rename_into_an_occupied_name_is_refused_before_writing(editor):
    rel = _write(
        editor,
        [
            _declaring_node(),
            _declaring_node(node_id="dt2", alias="taken"),
            _log("${old}"),
        ],
    )
    before = _target(editor).read_bytes()

    receipt = _rename(editor, rel, "dt1", value="taken")

    assert receipt["ok"] is False
    assert receipt["code"] == "INVALID_VALUE"
    assert "taken" in receipt["error"]
    # 拒绝 = 一个字都不写：半途改掉引用、别名却没落地是最难恢复的状态
    assert _target(editor).read_bytes() == before


def test_rename_into_a_loop_variable_name_is_refused(editor):
    """与循环变量同名不会报错，只会让循环体里的 `[row]` 静默取到别名值。"""
    rel = _write(
        editor,
        [
            _declaring_node(),
            {
                "type": "forEach",
                "id": "fe1",
                "items": "${old}",
                "item_var": "row",
                "children": [_log("[row]")],
            },
        ],
    )
    before = _target(editor).read_bytes()

    receipt = _rename(editor, rel, "dt1", value="row")

    assert receipt["ok"] is False
    assert receipt["code"] == "INVALID_VALUE"
    assert "循环" in receipt["error"]
    assert _target(editor).read_bytes() == before


# ---- 参数与别名同一笔写盘 ---------------------------------------------------
def test_save_params_carries_the_rename_in_one_write(editor):
    """前端只有「保存」一个按钮：参数与别名必须同一次提交、同一次编译。

    分两个 RPC 就会出现「参数存进去了、别名被拒了」的半成功状态，用户既不知道文件
    到哪一步，也没法撤销。
    """
    rel = _write(editor, [_declaring_node(), _log("${old}")])
    editor._flows.pop(rel, None)
    detail = editor.node_detail(rel, "dt1")
    params = {field["key"]: field["valueText"] for field in detail["fields"]}

    receipt = editor.save_node_params(
        rel,
        "dt1",
        params,
        expr_modes={},
        aliases={"value": "new", "iso": "", "timestamp": ""},
    )

    assert receipt["ok"], receipt
    assert receipt["renamed"] == {"old": "new"}
    document = _document(editor)
    assert _find(document, "dt1")["output_aliases"] == {"value": "new"}
    assert _find(document, "log1")["with"]["message"] == "${new}"


def test_alias_rejected_by_save_params_leaves_params_unwritten(editor):
    """别名闸门在写盘之前：别名不合法时，**参数也不许落地**（否则就是半成功）。"""
    rel = _write(editor, [_declaring_node(), _log("${old}")])
    editor._flows.pop(rel, None)
    detail = editor.node_detail(rel, "dt1")
    params = {field["key"]: field["valueText"] for field in detail["fields"]}
    params["format"] = "unix"  # 一次合法的参数改动，用来证明它没被写进去
    before = _target(editor).read_bytes()

    receipt = editor.save_node_params(
        rel,
        "dt1",
        params,
        expr_modes={},
        aliases={"value": "1bad", "iso": "", "timestamp": ""},
    )

    assert receipt["ok"] is False
    assert receipt["code"] == "INVALID_VALUE"
    assert _target(editor).read_bytes() == before


# ---- 面板需要的数据 --------------------------------------------------------
def test_node_detail_lists_output_rows_for_the_alias_inputs(editor):
    """`nodeDetail.outputs` 是别名输入行的唯一数据源：标签走 `x-outputs.label`。"""
    rel = _write(editor, [_declaring_node()])
    rows = {row["key"]: row for row in editor.node_detail(rel, "dt1")["outputs"]}

    assert set(rows) == {"value", "iso", "timestamp"}
    assert rows["value"]["label"] == "格式化时间"
    assert rows["value"]["alias"] == "old"
    assert rows["iso"]["alias"] == ""
    assert all(row["declared"] for row in rows.values())
