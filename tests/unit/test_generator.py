"""rpa_core.generator 的单元测试：生成、校验、错误处理。

不依赖任何浏览器/桌面运行时：只验证「生成的流程能否通过真实编译器 + 实例级 schema 校验」。
"""

import json

import pytest

from rpa_core.catalog import commands_root, load_catalog
from rpa_core.generator import (
    FlowGenerationError,
    FlowSpec,
    StepSpec,
    generate_from_spec,
    generate_from_text,
)
from rpa_core.generator.interpreter import KeywordInterpreter

CATALOG = load_catalog(commands_root())


def test_generate_from_text_produces_valid_flow():
    text = (
        "打开 https://www.baidu.com 然后 "
        "在 #kw 输入 WorkBuddy 然后 "
        "点击 #su 然后 "
        "截图保存"
    )
    flow = generate_from_text(text, id="demo_search")
    # 节点：navigate / input / click / screenshot
    nodes = flow.workflow.root.children
    assert [n.command for n in nodes] == [
        "browser.navigate",
        "browser.input",
        "browser.click",
        "browser.screenshot",
    ]
    # 会话自动串联：input/click/screenshot 都注入了 ${page_1}
    assert nodes[1].with_["sessionId"] == "${page_1}"
    assert nodes[2].with_["sessionId"] == "${page_1}"
    # 截图路径自动补全
    assert nodes[3].with_["savePath"] == "outputs/screenshot_1.png"
    # 编译器已通过（validate_generated 内部调用），plan 可用
    assert flow.plan.catalog_digest == CATALOG.digest


def test_explicit_spec_roundtrip():
    spec = FlowSpec(
        id="explicit_demo",
        name="显式流程",
        steps=[
            StepSpec(
                command="data.setVar",
                with_={"varName": "x", "value": 42, "varType": "number"},
            ),
            StepSpec(command="data.log", with_={"message": "x=${x}"}),
        ],
    )
    flow = generate_from_spec(spec)
    assert flow.workflow.root.children[0].command == "data.setVar"
    # 落盘后能被 Workflow 模型重新解析
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = flow.write(Path(d) / "sub" / "workflow.json")
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["id"] == "explicit_demo"
        assert raw["root"]["children"][0]["command"] == "data.setVar"


def test_unknown_command_rejected():
    spec = FlowSpec(steps=[StepSpec(command="no.such.command", with_={})])
    with pytest.raises(FlowGenerationError):
        generate_from_spec(spec)


def test_missing_selector_rejected():
    # click 没有可推断的选择器 → 报错，而不是吐出无法运行的流程
    with pytest.raises(FlowGenerationError):
        generate_from_text("点击提交按钮")


def test_invalid_enum_rejected():
    # browser.navigate 的 action 枚举不含 "fly"
    spec = FlowSpec(
        steps=[StepSpec(command="browser.navigate", with_={"action": "fly", "url": "https://x.com"})]
    )
    with pytest.raises(FlowGenerationError):
        generate_from_spec(spec)


def test_unknown_step_action_rejected():
    with pytest.raises(FlowGenerationError):
        generate_from_text("帮我倒杯咖啡")


def test_keyword_interpreter_navigate_url():
    interp = KeywordInterpreter()
    step = interp.interpret("打开 https://example.com", catalog=CATALOG)
    assert step is not None
    assert step.command == "browser.navigate"
    assert step.with_["url"] == "https://example.com"


def test_keyword_interpreter_site_alias():
    interp = KeywordInterpreter()
    step = interp.interpret("打开百度", catalog=CATALOG)
    assert step is not None
    assert step.with_["url"] == "https://www.baidu.com"
