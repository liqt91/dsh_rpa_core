"""流程 `inputs` 声明模型契约（M26 S1）。

`sig` 面：`Workflow.inputs` 是 **`{名称: 默认值}` 扁平映射**（无 type/required/description），
GUI 的运行参数对话框按它渲染一行 `QLineEdit`、按 JSON 解析。本模块（`model/inputs.py`）
只做规范化 + 校验，**不引入第二份声明格式**——所以这里最重要的一组测试是
「**往返保真**」：任何既有声明经编辑往返后必须逐字节等价，否则打开一次流程就会改坏它。

三条设计约束在这里被钉住：

1. **形状兼容**：`entries_from_declaration` → `declaration_from_entries` 必须恒等
   （含 `null` / 嵌套对象 / 数组 / 中文 / 布尔 / 浮点）；
2. **名称规则比 `Workflow.id` 严**：`[A-Za-z_]\\w*`，**不含点号**——因为 `${...}` 把点号当
   路径分隔符，名字里带点会导致「声明了但永远引用不上」；
3. **保留名拦截**：`inputs` / `steps` / `loop` 与 `compiler._RESERVED_ALIAS_ROOTS` 同口径，
   刻意不 import 该常量（它是编译器的私有实现细节），而是**用测试断言两边一致**——
   这样编译器改了保留名集合，这里会红，而不是悄悄漂移。
"""

from __future__ import annotations

import json

import pytest

from rpa_core.compiler.compiler import _RESERVED_ALIAS_ROOTS
from rpa_core.model.inputs import (
    INPUT_NAME_PATTERN,
    MAX_INPUT_NAME_LENGTH,
    RESERVED_INPUT_NAMES,
    InputDeclarationError,
    InputEntry,
    declaration_from_entries,
    entries_from_declaration,
    referenced_input_names,
    validate_declaration,
    validate_entries,
    validate_input_name,
)

# ------------------------------------------------------- 1. 与编译器口径保持一致


def test_reserved_names_match_the_compiler():
    """保留名集合必须与 `compiler._RESERVED_ALIAS_ROOTS` 一致。

    刻意 import 编译器的私有常量来断言（而非复制一份字面量）：编译器若调整保留名，
    这里立刻红，而不是两处清单悄悄分叉。
    """
    assert RESERVED_INPUT_NAMES == frozenset(_RESERVED_ALIAS_ROOTS)
    assert RESERVED_INPUT_NAMES == {"inputs", "steps", "loop"}


def test_name_pattern_is_stricter_than_workflow_id_pattern():
    """输入名比 `Workflow.id` 严：**不允许点号**（点号是 `${...}` 的路径分隔符）。"""
    from rpa_core.model.workflow import Workflow

    id_pattern = Workflow.model_fields["id"].metadata
    # Workflow.id 允许点号（它是文件/流程标识），输入名不允许（它是引用标识符）
    assert INPUT_NAME_PATTERN.match("a.b") is None
    assert id_pattern  # 只为说明两者是不同角色，不依赖其内部结构
    # 共同点：都要求字母/下划线开头
    assert INPUT_NAME_PATTERN.match("_x") is not None
    assert INPUT_NAME_PATTERN.match("1x") is None


# --------------------------------------------------------------- 2. 名称合法性


@pytest.mark.parametrize("name", ["a", "_a", "a1", "userName", "USER_NAME", "_", "a" * 64])
def test_valid_names_are_accepted(name):
    validate_input_name(name)  # 不抛即通过


@pytest.mark.parametrize(
    "name,reason",
    [
        ("", "空"),
        ("1a", "数字开头"),
        ("a b", "含空格"),
        ("a-b", "含连字符"),
        ("a.b", "含点号（会与 ${...} 路径文法冲突）"),
        ("a[0]", "含方括号"),
        ("中文名", "非 ASCII"),
        ("a" * 65, "超长"),
        ("inputs", "保留名"),
        ("steps", "保留名"),
        ("loop", "保留名"),
    ],
)
def test_invalid_names_are_rejected(name, reason):
    with pytest.raises(InputDeclarationError):
        validate_input_name(name)


def test_overlong_name_boundary():
    """长度边界：64 合法、65 非法（只挡荒谬输入，不是格式要求）。"""
    validate_input_name("a" * MAX_INPUT_NAME_LENGTH)
    with pytest.raises(InputDeclarationError):
        validate_input_name("a" * (MAX_INPUT_NAME_LENGTH + 1))


def test_reserved_name_error_mentions_the_alternative():
    """报错要说清「为什么不行」，而不是只说「不行」。"""
    with pytest.raises(InputDeclarationError) as excinfo:
        validate_input_name("steps")
    message = str(excinfo.value)
    assert "内置作用域根名" in message
    assert "inputs" in message and "loop" in message


# -------------------------------------------------------- 3. 默认值的 JSON 语义


def test_empty_text_means_null_default():
    """空文本 = 默认值 `null`（与运行参数对话框的「空 = 沿用默认」是两回事）。

    这里编辑的是**声明本身**，声明必须落盘，所以不能把「空」理解成「不写这个键」。
    """
    assert InputEntry(name="x", json_text="").default_value is None
    assert InputEntry(name="x", json_text="   ").default_value is None
    assert declaration_from_entries([InputEntry(name="x", json_text="")]) == {"x": None}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1", 1),
        ('"s"', "s"),
        ("true", True),
        ("false", False),
        ("null", None),
        ("1.5", 1.5),
        ("[1,2]", [1, 2]),
        ('{"a":1}', {"a": 1}),
        ("中文", None),  # 非法 JSON → 抛，见下一条测试
    ],
)
def test_default_value_parses_json(text, expected):
    if expected is None and text == "中文":
        with pytest.raises(InputDeclarationError):
            _ = InputEntry(name="x", json_text=text).default_value
        return
    assert InputEntry(name="x", json_text=text).default_value == expected


def test_invalid_json_default_is_rejected_with_the_name():
    """非法 JSON 报错要带上**是哪个输入**坏了，否则表格里多个错分不清。"""
    with pytest.raises(InputDeclarationError) as excinfo:
        _ = InputEntry(name="threshold", json_text="{oops").default_value
    assert "threshold" in str(excinfo.value)


def test_bare_word_is_not_valid_json():
    """裸词（`abc`）不是合法 JSON——GUI 里最容易犯的错，必须报而不是当成字符串。"""
    with pytest.raises(InputDeclarationError):
        _ = InputEntry(name="x", json_text="abc").default_value


def test_json_text_is_normalized_for_stable_bytes():
    """规范化文本：对象键序稳定（否则每次保存字节都变，脏标记与 git diff 会被污染）。"""
    left = InputEntry(name="x", json_text='{"b": 1, "a": 2}').to_json_text()
    right = InputEntry(name="x", json_text='{"a":2,"b":1}').to_json_text()
    assert left == right == '{"a": 2, "b": 1}'


# ------------------------------------------------------------ 4. 往返保真（核心）


@pytest.mark.parametrize(
    "declaration",
    [
        {},
        {"a": None},
        {"a": 1, "b": "s", "c": True, "d": 1.5},
        {"list": [1, [2, 3], {"k": "v"}]},
        {"obj": {"nested": {"deep": [1, 2]}}},
        {"cn": "允许非 ASCII 的**值**"},
        {"a" * 64: "边界长度的名字"},
    ],
)
def test_declaration_roundtrip_is_exact(declaration):
    """**核心约束**：往返必须恒等，否则「打开流程再保存」就会改坏既有文件。

    注意夹具是「合法**名** + 非 ASCII **值**」：名字必须是 `[A-Za-z_]\\w*`
    （中文名不合法，见 `test_invalid_names_are_rejected`），但值可以是任意 JSON。
    """
    entries = entries_from_declaration(declaration)
    assert declaration_from_entries(entries) == declaration


def test_roundtrip_preserves_null_distinctly_from_missing():
    """`{"a": null}` 与 `{}` 是两种声明，往返后不得混淆。"""
    assert declaration_from_entries(entries_from_declaration({"a": None})) == {"a": None}
    assert declaration_from_entries(entries_from_declaration({})) == {}


def test_entries_are_sorted_by_name_for_stable_ui():
    """条目按名称排序：界面行序与保存结果可复现（dict 本身有序但不代表语义顺序）。"""
    entries = entries_from_declaration({"zeta": 1, "alpha": 2, "mid": 3})
    assert [e.name for e in entries] == ["alpha", "mid", "zeta"]


def test_none_and_empty_declaration_both_give_no_entries():
    assert entries_from_declaration(None) == []
    assert entries_from_declaration({}) == []


def test_reading_is_lenient_but_writing_is_strict():
    """读取既有文件要宽松（历史声明不合法也不该打不开），保存要严格。

    这条区分很重要：若读取就抛，用户会卡在「文件打不开、也改不了」的死局里。
    """
    from rpa_core.model.inputs import InputDeclarationError as Err

    # 读取：非法名（点号）也能读出条目，不抛
    entries = entries_from_declaration({"a.b": 1})
    assert entries == [InputEntry(name="a.b", json_text="1")]
    # 保存：同一份内容被拒
    with pytest.raises(Err):
        declaration_from_entries(entries)


# ------------------------------------------------------------ 5. 整份校验


def test_duplicate_names_are_rejected():
    with pytest.raises(InputDeclarationError) as excinfo:
        validate_entries(
            [InputEntry(name="x", json_text="1"), InputEntry(name="x", json_text="2")]
        )
    assert "重复" in str(excinfo.value)


def test_all_problems_are_reported_at_once():
    """一次报全部问题：GUI 表格可能有多个错，不该「改一个保存一次再报下一个」。"""
    with pytest.raises(InputDeclarationError) as excinfo:
        validate_entries(
            [
                InputEntry(name="1bad", json_text="1"),
                InputEntry(name="steps", json_text="1"),
                InputEntry(name="ok", json_text="{oops"),
            ]
        )
    message = str(excinfo.value)
    assert "1bad" in message
    assert "steps" in message
    assert "ok" in message


def test_multiple_bad_names_all_appear_in_one_error():
    with pytest.raises(InputDeclarationError) as excinfo:
        validate_entries(
            [InputEntry(name="a-b", json_text="1"), InputEntry(name="c d", json_text="1")]
        )
    message = str(excinfo.value)
    assert "a-b" in message and "c d" in message


def test_validate_declaration_accepts_normal_shapes():
    validate_declaration({})
    validate_declaration(None)
    validate_declaration({"a": 1, "b": None, "c": {"x": [1]}})


def test_validate_declaration_rejects_bad_names():
    with pytest.raises(InputDeclarationError):
        validate_declaration({"a.b": 1})
    with pytest.raises(InputDeclarationError):
        validate_declaration({"inputs": 1})


def test_valid_entries_pass():
    validate_entries(
        [
            InputEntry(name="a", json_text="1"),
            InputEntry(name="b", json_text=""),
            InputEntry(name="c", json_text='{"k": 1}'),
        ]
    )


# ------------------------------------------------- 6. 引用提取（供 S3 找残留引用）


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("inputs.a", "a"),
        ("inputs.user.name", "user"),
        ("steps.x.outputs.y", None),
        ("loop.item", None),
        ("alias.field", None),
        ("inputs", None),
        ("", None),
    ],
)
def test_referenced_input_names(reference, expected):
    assert referenced_input_names(reference) == expected


# --------------------------------------------- 7. 与真实 Workflow 形状的一致性


def test_workflow_inputs_field_accepts_what_we_produce():
    """我们产出的声明必须能被 `Workflow` 原样接受（形状是真的兼容，不是声称兼容）。"""
    from rpa_core.model.workflow import Workflow

    declaration = declaration_from_entries(
        [
            InputEntry(name="url", json_text='"https://example.com"'),
            InputEntry(name="retries", json_text="3"),
            InputEntry(name="opts", json_text='{"a": 1}'),
            InputEntry(name="nothing", json_text=""),
        ]
    )
    workflow = Workflow.model_validate(
        {
            "id": "t",
            "name": "t",
            "inputs": declaration,
            "root": {"type": "sequence", "id": "root", "children": []},
        }
    )
    assert workflow.inputs == declaration


def test_declaration_survives_workflow_dump_roundtrip():
    """经 pydantic 序列化也不变形（GUI 保存走的就是这条路）。"""
    from rpa_core.model.workflow import Workflow

    declaration = {"a": None, "b": [1, {"k": "v"}], "中文": "值"}
    workflow = Workflow.model_validate(
        {
            "id": "t",
            "name": "t",
            "inputs": declaration,
            "root": {"type": "sequence", "id": "root", "children": []},
        }
    )
    dumped = workflow.model_dump(mode="json")
    assert dumped["inputs"] == declaration


def test_compiler_accepts_reference_to_a_declared_input():
    """端到端形状验证：按我们产出的声明，编译器认 `${inputs.名}` 引用。"""
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler.compiler import WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")

    declaration = declaration_from_entries([InputEntry(name="url", json_text='"http://x"')])
    workflow = Workflow.model_validate(
        {
            "id": "t",
            "name": "t",
            "inputs": declaration,
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
    )
    WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))  # 不抛即通过


def test_compiler_rejects_reference_to_an_undeclared_input():
    """反向：删掉声明后残留引用必须如实报错（S3 的验收点之一，这里先钉住编译器行为）。"""
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")

    workflow = Workflow.model_validate(
        {
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
    )
    with pytest.raises(WorkflowCompileError, match="Unknown workflow input"):
        WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


def test_compiler_rejects_a_reserved_input_name():
    """声明占用保留名 → 编译期拒绝（否则 `${inputs.inputs}` 之类的引用会产生歧义）。

    这是 S1 接入编译路径的验收点：`validate_declaration` 不只是躺在库里的纯函数。
    """
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")

    workflow = Workflow.model_validate(
        {
            "id": "t",
            "name": "t",
            "inputs": {"steps": 1},
            "root": {"type": "sequence", "id": "root", "children": []},
        }
    )
    with pytest.raises(WorkflowCompileError, match="Invalid workflow inputs declaration"):
        WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


def test_compiler_rejects_a_dotted_input_name():
    """名字含点号 → 编译期拒绝：声明在册但 `${inputs.a.b}` 永远引用不上（静默失败更坏）。"""
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")

    workflow = Workflow.model_validate(
        {
            "id": "t",
            "name": "t",
            "inputs": {"a.b": 1},
            "root": {"type": "sequence", "id": "root", "children": []},
        }
    )
    with pytest.raises(WorkflowCompileError, match="Invalid workflow inputs declaration"):
        WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


def test_compiler_still_accepts_a_normal_declaration():
    """回归：正常声明不被新校验误伤（空声明与常规声明都要能过）。"""
    from rpa_core.catalog import load_catalog
    from rpa_core.compiler.compiler import WorkflowCompiler
    from rpa_core.devserver.app import DEFAULT_CAPABILITIES
    from rpa_core.model.workflow import Workflow

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")

    for declaration in ({}, {"url": "http://x", "retries": 3, "opt": None}):
        workflow = Workflow.model_validate(
            {
                "id": "t",
                "name": "t",
                "inputs": declaration,
                "root": {"type": "sequence", "id": "root", "children": []},
            }
        )
        WorkflowCompiler(catalog).compile(workflow, set(DEFAULT_CAPABILITIES))


def test_json_default_text_survives_a_non_ascii_value():
    """非 ASCII 值不转义（`ensure_ascii=False`）：界面上要看到中文本身。"""
    entry = InputEntry(name="x", json_text='"中文"')
    assert entry.to_json_text() == '"中文"'
    assert json.loads(entry.to_json_text()) == "中文"
