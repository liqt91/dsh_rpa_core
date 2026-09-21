"""流程输入声明与消费方的联动契约（M26 S3）。

S2 把声明做成了可编辑的 `_workflow_meta["inputs"]`；本文件验证**三个消费方真的跟着变**，
而不是「看起来接线了」：

| 消费方 | 实现位置 | 期望行为 |
|---|---|---|
| 运行参数对话框 | `RunInputsDialog(declared)` | 新增声明后立即出现一行；删除后不再出现 |
| 变量面板 / `${` 补全 | `_collect_reference_paths()` | 列出 `inputs.<名>`；删除后消失 |
| 编译期引用校验 | `compiler.validate_refs` | 删除声明后残留引用**如实报错**（不静默） |

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。

最后一类是**跨层一致性**：`_collect_reference_paths` 产出的 `inputs.<名>` 必须真的是编译器
认的引用形态——两处自己实现一遍「什么算合法引用」是这类功能的经典漂移点。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip(
    "PySide6", reason="GUI 契约测试需要 gui extra（uv sync --all-groups --extra gui）"
)

from rpa_core.model.inputs import INPUT_NAME_PATTERN  # noqa: E402


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


def _set_inputs(window, declaration: dict) -> None:
    """模拟「编辑声明并确定」（S2 的落点）。"""
    window._workflow_meta["inputs"] = declaration


# ------------------------------------------- 1. 运行参数对话框跟着声明走


def test_run_dialog_renders_one_row_per_declared_input(qapp):
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"url": "http://x", "retries": 3})
    assert set(dialog._edits) == {"url", "retries"}


def test_run_dialog_reflects_a_newly_added_input(qapp):
    """S3 验收点：新增输入后运行对话框**立即**出现该行。"""
    from rpa_core.gui.app import RunInputsDialog

    before = RunInputsDialog({"url": "http://x"})
    assert "retries" not in before._edits

    after = RunInputsDialog({"url": "http://x", "retries": 3})
    assert "retries" in after._edits


def test_run_dialog_drops_a_removed_input(qapp):
    """反向：删除声明后对话框不再出现该行。"""
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"url": "http://x"})
    assert "retries" not in dialog._edits


def test_run_dialog_shows_the_default_as_placeholder(qapp):
    """默认值进 placeholder（空文本 = 沿用默认），这是对话框的既有契约。"""
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"retries": 3})
    assert "3" in dialog._edits["retries"].placeholderText()


def test_run_dialog_values_parses_json_and_skips_blanks(qapp):
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"a": 1, "b": None})
    dialog._edits["a"].setText("42")
    # b 留空 → 不覆盖（沿用声明默认值）
    assert dialog.values() == {"a": 42}


def test_run_dialog_rejects_invalid_json(qapp):
    """非法 JSON 在 values() 抛 ValueError（既有行为，声明编辑器不该改变它）。"""
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({"a": 1})
    dialog._edits["a"].setText("{oops")
    with pytest.raises(ValueError):
        dialog.values()


def test_empty_declaration_means_no_dialog_is_needed(qapp):
    """空声明 → 无输入可填（`_start_run` 会跳过对话框），行为与修复前一致。"""
    from rpa_core.gui.app import RunInputsDialog

    dialog = RunInputsDialog({})
    assert dialog._edits == {}


# ------------------------------------------- 2. 变量面板 / 补全列出 inputs.<名>


def test_reference_paths_include_declared_inputs(window):
    _set_inputs(window, {"url": "http://x", "retries": 3})
    paths = window._collect_reference_paths()

    assert "inputs.url" in paths
    assert "inputs.retries" in paths


def test_reference_paths_update_when_an_input_is_added(window):
    """S3 验收点：新增声明后补全立即多出一条路径。"""
    _set_inputs(window, {"url": "http://x"})
    before = window._collect_reference_paths()
    assert "inputs.url" in before
    assert "inputs.timeout" not in before

    _set_inputs(window, {"url": "http://x", "timeout": 30})
    after = window._collect_reference_paths()
    assert "inputs.timeout" in after


def test_reference_paths_drop_deleted_inputs(window):
    _set_inputs(window, {"url": "http://x"})
    _set_inputs(window, {})
    paths = window._collect_reference_paths()

    assert not [p for p in paths if p.startswith("inputs.")]


def test_reference_paths_tolerate_a_missing_declaration(window):
    """没有 inputs 键（旧流程文件）也要能工作，不抛。"""
    window._workflow_meta.pop("inputs", None)
    assert isinstance(window._collect_reference_paths(), list)


def test_variable_panel_refresh_after_edit(window, monkeypatch):
    """改完声明要立刻刷新变量面板（否则用户看不到新入参）。"""
    refreshed: list[bool] = []
    monkeypatch.setattr(window, "_refresh_variables", lambda: refreshed.append(True))
    from rpa_core.gui import app as app_module

    monkeypatch.setattr(
        app_module, "flow_inputs_prompt", lambda current, parent=None: {"a": 1}
    )
    window._edit_flow_inputs()
    assert refreshed == [True]


# ------------------------------- 3. 跨层一致性：补全产出的路径要真的是合法引用


def test_generated_reference_paths_match_the_compiler_reference_grammar(window):
    """`inputs.<名>` 必须能被 `compiler._REFERENCE` 完整匹配。

    这是跨层一致性的关键断言：若名称规则与引用文法分叉（例如某天允许了带点的名字），
    补全会产出编译器认不出的字符串——用户点了补全反而拿到编译错误。
    """
    from rpa_core.compiler.compiler import _REFERENCE

    _set_inputs(window, {"url": "http://x", "retries": 3})
    for name in ("url", "retries"):
        assert INPUT_NAME_PATTERN.match(name), name
        assert _REFERENCE.match(f"${{inputs.{name}}}"), name


def test_every_declared_name_survives_a_compiler_roundtrip(window):
    """端到端：声明 → 引用 → 编译通过（把两层真的接上，而不是各自单测）。"""
    from rpa_core.compiler.compiler import WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    _set_inputs(window, {"url": "http://x"})
    document = window._build_document()
    assert document is not None

    document["root"] = {
        "type": "sequence",
        "id": "root",
        "children": [
            {
                "type": "action",
                "id": "a1",
                "command": "browser.navigate",
                "with": {"url": "${inputs.url}", "browserType": "msedge"},
            }
        ],
    }
    workflow = Workflow.model_validate(document)
    WorkflowCompiler(window.catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


# ------------------------------------------- 4. 删除声明后的残留引用如实报错


def test_deleting_a_declaration_makes_the_residual_reference_fail(window):
    """S3 验收点：删除声明后残留引用**在编译校验里如实报错**（不静默）。"""
    from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    document = {
        "schema_version": "1.0",
        "id": "t",
        "name": "t",
        "inputs": {},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "a1",
                    "command": "browser.navigate",
                    "with": {"url": "${inputs.url}", "browserType": "msedge"},
                }
            ],
        },
    }
    workflow = Workflow.model_validate(document)
    with pytest.raises(WorkflowCompileError, match="Unknown workflow input"):
        WorkflowCompiler(window.catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


def test_validate_action_surfaces_the_residual_reference(window):
    """GUI 的「校验」入口要能看到这条错误（而不是只在编译期静默失败）。"""
    from rpa_core.gui.app import collect_validation_issues
    from rpa_core.model.workflow import Workflow

    document = {
        "schema_version": "1.0",
        "id": "t",
        "name": "t",
        "inputs": {},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "a1",
                    "command": "browser.navigate",
                    "with": {"url": "${inputs.url}", "browserType": "msedge"},
                }
            ],
        },
    }
    Workflow.model_validate(document)  # 结构本身合法
    issues = collect_validation_issues(document, window.catalog)

    assert any("inputs.url" in message for message, _node in issues), issues


def test_validate_action_is_clean_when_the_input_is_declared(window):
    """回归：声明还在时「校验」不该报错（否则这个功能没法用）。"""
    from rpa_core.gui.app import collect_validation_issues
    from rpa_core.model.workflow import Workflow

    document = {
        "schema_version": "1.0",
        "id": "t",
        "name": "t",
        "inputs": {"url": "http://x"},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "a1",
                    "command": "browser.navigate",
                    "with": {"url": "${inputs.url}", "browserType": "msedge"},
                }
            ],
        },
    }
    Workflow.model_validate(document)
    issues = collect_validation_issues(document, window.catalog)
    assert not [m for m, _ in issues if "inputs.url" in m], issues


# ------------------------------------------------- 5. 与历史输入的交互（M25）


def test_history_inputs_are_passed_through_unfiltered(window, monkeypatch):
    """M25「用历史输入再跑」：历史 inputs 可能含**已删除的声明**——不被静默丢弃。

    这条钉住的是「**声明不是运行输入的过滤器**」：声明只用于渲染表单与编译期校验，
    回放历史输入时按历史原样透传（任务单 §风险 明确要求）。

    做法：给 `read_run` 打桩返回含 `legacy` 的历史 inputs（当前声明里没有它），
    观察 `_start_run` 收到的是**完整历史输入**而非被声明裁剪后的子集。
    """
    import rpa_core.run_history as run_history

    _set_inputs(window, {"url": "http://x"})  # 当前声明里没有 legacy

    history_inputs = {"url": "http://old", "legacy": "keep-me"}
    monkeypatch.setattr(
        run_history, "read_run", lambda root, run_id: {"inputs": history_inputs}
    )
    monkeypatch.setattr(window, "_selected_history_run", lambda: {"runId": "r1", "workflowId": "t"})
    monkeypatch.setattr(window, "_flow_name_for_run", lambda run: "t")
    monkeypatch.setattr(window, "_open_named_flow", lambda name: None)

    captured: dict = {}
    monkeypatch.setattr(
        window, "_start_run", lambda name, inputs=None: captured.update(inputs or {}) or "run-2"
    )
    window._rerun_history_run()

    assert captured == history_inputs, "历史输入被按当前声明裁剪了——已删除的声明不该影响回放"


def test_history_rerun_announces_the_input_count(window, monkeypatch):
    """再跑要如实报输入条数（含已删除声明的那些），用户才知道实际下发了什么。"""
    import rpa_core.run_history as run_history

    monkeypatch.setattr(
        run_history, "read_run", lambda root, run_id: {"inputs": {"a": 1, "legacy": 2}}
    )
    monkeypatch.setattr(window, "_selected_history_run", lambda: {"runId": "r1", "workflowId": "t"})
    monkeypatch.setattr(window, "_flow_name_for_run", lambda run: "t")
    monkeypatch.setattr(window, "_open_named_flow", lambda name: None)
    monkeypatch.setattr(window, "_start_run", lambda name, inputs=None: "run-3")

    messages: list[str] = []
    monkeypatch.setattr(
        window.statusBar(), "showMessage", lambda text, ms=0: messages.append(text)
    )
    window._rerun_history_run()

    assert any("2 项" in m for m in messages), messages
