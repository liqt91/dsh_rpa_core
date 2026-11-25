"""编辑器参数表单的端到端契约（M108）：中文标签 / 表达式模式 / 落盘清理。

对齐影刀参数机制时暴露的问题不是「缺一个开关」，而是**参数的四层里有三层没有接线**：

1. 定义层（manifest 的 `input_schema` + `x-*`）：很完整，甚至比影刀细；
2. 标签层（`gui/i18n.js`）：全仓唯一事实源，但**没有任何 UI 消费者**——
   编辑器把 `label` 写死成键名，于是 324 个字段在界面上是 `postDelayMs` 这种英文；
3. 模式层（`_exprModes`）：运行期认 fx/python，前端却一个入口都没有，
   用户只能手改 JSON；
4. 提交层（`save_node_params` 七道闸）：不收 `_exprModes`，于是手改出来的模式
   在下一次保存时被**原样留下但不再受控**（连清理都没人做）。

本文件的判据全部走**真实 RPC**（`ppx_py` 打桩后直接调 `api.py` 的 handler），不是
纯函数直调——参数表单的 bug 从来不是算错，而是「这条路径没人走」。
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
    spec = importlib.util.spec_from_file_location("editor_api_param_form", API)
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


def _make_flow(api, nodes, inputs=None) -> str:
    """在临时工作区落一份流程，返回相对路径（`_flow_path` 认这种口径）。"""
    rel = "workflows/probe/workflow.json"
    target = api.WORKSPACE / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": "1.0",
        "id": "param.form",
        "name": "param form",
        "inputs": inputs or {"who": "世界"},
        "root": {"type": "sequence", "id": "root", "children": nodes},
    }
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 每次重写都是一份新文件：丢掉 `_flows` 里的旧 SHA，否则保存会被 FLOW_CHANGED 闸
    # 拦下（那道闸本身是对的——它防的是覆盖别人在编辑器外做的修改）。
    api._flows.pop(rel, None)
    return rel


def _log_node(node_id="log1", message="你好") -> dict:
    return {"type": "action", "id": node_id, "command": "data.log", "with": {"message": message}}


def _node(api, rel: str) -> dict:
    path = api.WORKSPACE / rel
    return json.loads(path.read_text(encoding="utf-8"))["root"]["children"][0]


# ---- 标签层 -----------------------------------------------------------------
def test_field_labels_come_from_i18n_not_from_the_key(editor):
    """字段标签必须是中文（来自 `gui/i18n.js`），不是键名。

    `label == key` 是 M108 之前的实际行为：`i18n.js` 里的 fields/commandFields 只有
    生成脚本在读，缺一条标签完全无症状。判据写「等值」而不是「至少有一半是中文」——
    阈值型判据容得下几十个字段裸显示英文键名。
    """
    rel = _make_flow(editor, [_log_node()])
    detail = editor.node_detail(rel, "log1")
    labels = {f["key"]: f["label"] for f in detail["fields"]}
    assert labels["message"] == "日志内容"
    assert labels["level"] == "日志级别"
    assert all(labels[key] != key for key in labels), labels


def test_field_metadata_carries_expression_capability(editor):
    """`fx` / `python` / `mode` / `varWrite` 由 manifest 声明算出，不是前端猜的。"""
    rel = _make_flow(
        editor,
        [
            {"type": "action", "id": "set1", "command": "data.setVar",
             "with": {"varName": "who2", "value": "x"}},
            _log_node(),
        ],
    )
    log_fields = {f["key"]: f for f in editor.node_detail(rel, "log1")["fields"]}
    assert log_fields["message"]["fx"] is True
    assert log_fields["level"]["fx"] is False
    assert log_fields["message"]["mode"] == ""

    setv = {f["key"]: f for f in editor.node_detail(rel, "set1")["fields"]}
    # 「是不是变量写入目标」认 manifest 的 x-var-write，不认命令 id
    assert setv["varName"]["varWrite"] is True
    assert setv["value"]["varWrite"] is False
    assert setv["value"]["fx"] is True


# ---- 模式层 + 提交层 --------------------------------------------------------
def test_save_persists_fx_mode_and_runtime_resolves_it(editor):
    """保存 fx 模式 → 文件里有 `_exprModes`，且运行期的标签替换真能取到值。"""
    rel = _make_flow(editor, [_log_node(message="你好[inputs.who]")])
    result = editor.save_node_params(
        rel, "log1", {"message": "你好[inputs.who]", "level": ""},
        expr_modes={"message": "fx", "level": ""},
    )
    assert result["ok"] is True, result
    assert _node(editor, rel)["_exprModes"] == {"message": "fx"}

    from rpa_core.runtime.resolver import resolve_tags

    assert resolve_tags("你好[inputs.who]", {"inputs": {"who": "世界"}}) == "你好世界"


def test_save_clears_mode_when_no_tag_left(editor):
    """标签被删光 ⇒ 自动撤销 fx 态（不报错，也不能留一个空模式）。

    这是「用户把最后一个标签删掉」的正常操作序列。留着空 fx 态不会让运行期出错
    （`resolve_tags` 对无标签字符串原样返回），但它会在下次打开面板时显示「fx 开着」，
    而字段里一个变量都没有——没人解释得清这是什么状态。
    """
    rel = _make_flow(editor, [_log_node(message="你好[inputs.who]")])
    editor.save_node_params(
        rel, "log1", {"message": "你好[inputs.who]", "level": ""},
        expr_modes={"message": "fx"},
    )
    assert "_exprModes" in _node(editor, rel)

    result = editor.save_node_params(
        rel, "log1", {"message": "你好", "level": ""}, expr_modes={"message": "fx"}
    )
    assert result["ok"] is True, result
    assert "_exprModes" not in _node(editor, rel)
    assert result["modesCleared"] == ["message"]


def test_save_removes_empty_mode_map_entirely(editor):
    """全部模式都被撤销 ⇒ 整个 `_exprModes` 键消失（不留 `{}` 噪声）。"""
    rel = _make_flow(editor, [_log_node(message="你好[inputs.who]")])
    editor.save_node_params(
        rel, "log1", {"message": "你好[inputs.who]", "level": ""},
        expr_modes={"message": "fx"},
    )
    result = editor.save_node_params(
        rel, "log1", {"message": "", "level": ""}, expr_modes={"message": "fx"}
    )
    assert result["ok"] is True, result
    assert "_exprModes" not in _node(editor, rel)


def test_save_rejects_mode_not_declared_by_manifest(editor):
    """字段没声明 `x-fx` 就不许设 fx，而且**一个字都不许写盘**。

    这条挡的是前端 bug 与手改文件：能力由 manifest 决定，界面不该能造出一个
    声明里没有的模式。判据要连带核「文件逐字节未变」——只断言「返回了错误」的话，
    一个先写盘再返回错误的实现照样能过。
    """
    rel = _make_flow(editor, [_log_node()])
    before = (editor.WORKSPACE / rel).read_bytes()
    result = editor.save_node_params(
        rel, "log1", {"message": "你好", "level": "info"},
        expr_modes={"level": "fx"},
    )
    assert result["ok"] is False
    assert result["code"] == "INVALID_VALUE"
    assert "x-fx" in result["error"]
    assert (editor.WORKSPACE / rel).read_bytes() == before


def test_save_rejects_unknown_mode_value(editor):
    """`js` 这种值运行期会被当普通模式（静默降级）⇒ 提交时就拒。"""
    rel = _make_flow(editor, [_log_node()])
    result = editor.save_node_params(
        rel, "log1", {"message": "你好", "level": ""}, expr_modes={"message": "js"}
    )
    assert result["ok"] is False and result["code"] == "INVALID_VALUE"
    assert "js" in result["error"]


def test_compile_reports_unknown_fx_tag(editor):
    """fx 标签引用了不存在的变量 ⇒ 保存后的编译结果必须红（而不是等运行期）。

    保存语义仍是「落盘 + 回报编译结果」（与既有类型错误一致），但回报必须是红：
    前端把 `compile.ok === false` 显示出来，用户当场就知道 `[nope]` 打错了。
    """
    rel = _make_flow(editor, [_log_node(message="值=[nope]")])
    result = editor.save_node_params(
        rel, "log1", {"message": "值=[nope]", "level": ""}, expr_modes={"message": "fx"}
    )
    assert result["ok"] is True, result
    assert result["compile"]["ok"] is False, result["compile"]
    assert "[nope]" in result["compile"]["error"]


# ---- 变量菜单 ---------------------------------------------------------------
def _scoped_flow(editor, target: str = "", message: str = "x") -> str:
    """一个同时有「前序变量 / 循环 / catch」的流程，用来验菜单的三种作用域。

    ``target`` 指定把 ``message`` 写进哪个节点——只写给目标节点。把 `loop.row` 同时
    写给 catch 里的节点会**正确地**编译红（出了循环体 loop.row 不可见），那会让
    「菜单项能不能编译」的判据测到别的东西上。
    """
    def log(node_id: str) -> dict:
        return _log_node(node_id, message if node_id == target else "x")

    return _make_flow(
        editor,
        [
            {"type": "action", "id": "set1", "command": "data.setVar",
             "with": {"varName": "alpha", "value": "1"}},
            {
                "type": "forEach", "id": "loop1", "items": [1], "item_var": "row",
                "children": [log("logIn")],
            },
            {"type": "try", "id": "try1", "children": [], "catch": [log("logCatch")]},
        ],
    )


def test_variable_menu_uses_compiler_visible_scope(editor):
    """菜单内容 = `visible_scope` 的三组，各自带正确的引用形态。

    循环变量必须是 ``loop.row``：编译器只认这个形态，裸 ``row`` 会报
    `Unsupported reference root`。第一版判据就写错过这一点，所以这里写成**等值**断言。
    """
    rel = _scoped_flow(editor)
    inside_loop = editor.variable_scopes(rel, "logIn")
    refs = {
        group["label"]: [item["ref"] for item in group["items"]]
        for group in inside_loop["groups"]
    }
    assert refs["流程参数"] == ["inputs.who"]
    assert refs["变量"] == ["alpha"]
    assert refs["循环变量"] == ["loop.row"]
    assert "错误变量" not in refs  # 循环体内不在 catch 里，error 不可见

    in_catch = editor.variable_scopes(rel, "logCatch")
    catch_refs = {
        group["label"]: [item["ref"] for item in group["items"]]
        for group in in_catch["groups"]
    }
    assert catch_refs["错误变量"] == ["error"]
    assert "循环变量" not in catch_refs  # 出了循环体 ⇒ loop.row 不再可见


def test_variable_menu_hides_later_and_self_declarations(editor):
    """自身 / 更靠后节点声明的变量不进菜单（编译器也不会放行）。

    只验正例的话，一个「永远返回全流程变量」的实现也能全绿——所以这里要同时验
    「该出现的没出现」与「不该出现的确实不在」。
    """
    rel = _make_flow(
        editor,
        [
            {"type": "action", "id": "set1", "command": "data.setVar",
             "with": {"varName": "early", "value": "1"}},
            {"type": "action", "id": "set2", "command": "data.setVar",
             "with": {"varName": "late", "value": "2"}},
        ],
    )
    at_first = editor.variable_scopes(rel, "set1")
    refs = [item["ref"] for group in at_first["groups"] for item in group["items"]]
    assert "late" not in refs, "后置声明的变量进了菜单"
    assert "early" not in refs, "自身声明的变量进了菜单（读自己写入目标的新值是取不到的）"

    at_second = editor.variable_scopes(rel, "set2")
    refs2 = [item["ref"] for group in at_second["groups"] for item in group["items"]]
    assert "early" in refs2 and "late" not in refs2


def test_every_menu_entry_actually_compiles(editor):
    """菜单给出的**每一条**路径塞进该节点都必须编译通过。

    这是「菜单 ⊆ 编译通过」的端到端兑现点，也是唯一能发现「ref 形态写错」的判据：
    纯字符串比对口径会随实现一起漂，编译器不会。
    """
    rel = _scoped_flow(editor)
    checked = 0
    for node_id in ("logIn", "logCatch"):
        scopes = editor.variable_scopes(rel, node_id)
        for group in scopes["groups"]:
            for item in group["items"]:
                value = "${" + item["ref"] + "}"
                rel = _scoped_flow(editor, target=node_id, message=value)
                result = editor.save_node_params(rel, node_id, {"message": value, "level": ""})
                assert result["ok"] is True, (node_id, item, result)
                assert result["compile"]["ok"] is True, (node_id, item, result["compile"])
                checked += 1
    # 流程参数 + 变量 + 循环变量 / 错误变量，两个节点合计至少 5 条
    assert checked >= 5, f"只验证了 {checked} 条菜单项，判据没打中"

# ---- 分组层 / 联动层：声明必须真的到界面上 -----------------------------------
def test_groups_come_from_manifest_and_auto_group_absorbs_the_rest(editor):
    """分组是 manifest 声明的（不是前端硬编码的命令名表）。

    `auto: true` 的组织方式是刻意的：显式列字段的分组**必然**会在新增参数时把它漏进
    「其他」，而「其他」这个标题用户读不出是分组还是警告。所以这里同时钉住两件事：
    分组标签来自声明、且 auto 组真的兜住了没被认领的字段。
    """
    rel = _make_flow(
        editor,
        [{"type": "action", "id": "click1", "command": "browser.click",
          "with": {"selector": "#go"}}],
    )
    detail = editor.node_detail(rel, "click1")
    assert [group["label"] for group in detail["groups"]] == ["常规", "拟人化"]

    grouped = {}
    for field in detail["fields"]:
        grouped.setdefault(field["group"], set()).add(field["key"])
    # auto 组 = 常规：没被「拟人化」认领的字段都在这里，一个不漏
    assert grouped["常规"] == {
        "sessionId", "selector", "timeoutMs", "clickType", "button",
        "modifiers", "clickPosition", "postDelayMs",
    }
    assert grouped["拟人化"] == {
        "injection", "simulateHuman", "hoverMode", "pressDelayMs", "detachAfter",
    }
    assert "其他" not in grouped, "有字段没被任何组认领"


def test_depends_marks_the_cdp_only_fields(editor):
    """`x-depends` 必须到描述符里，且**只标**说明里真的写了「仅某模式生效」的字段。

    判据写成等值集合：少标一个 = 那个参数在 dom 通道下永远白填；多标一个 = 用户明明
    该改的参数被置灰，改不动。两种都比不标更耗信任。
    """
    rel = _make_flow(
        editor,
        [{"type": "action", "id": "click1", "command": "browser.click",
          "with": {"selector": "#go"}}],
    )
    detail = editor.node_detail(rel, "click1")
    depends = {field["key"]: field["depends"] for field in detail["fields"]}
    marked = {key for key, value in depends.items() if value}
    assert marked == {"simulateHuman", "hoverMode", "pressDelayMs", "detachAfter"}, marked
    assert depends["hoverMode"] == [{"key": "injection", "value": "cdp"}]
    assert depends["simulateHuman"] == [{"key": "injection", "value": "dom"}]
    assert depends["selector"] == []
