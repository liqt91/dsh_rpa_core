"""流程生成能力：把自然语言/结构化描述变成可被项目直接加载执行的流程文件。

设计约束（与现有代码风格一致）：
- 输入：自然语言描述（`--desc`）或结构化 spec（`--spec`）。
- 输出：`Workflow` 模型实例 + 落到磁盘的 `workflow.json`，
  可被 `rpa-core validate/run/gui` 直接消费。
- 校验：复用真实 `WorkflowCompiler`（引用/能力/别名/节点 id），再逐节点用命令 manifest 的
  `input_schema` 做**实例级**校验（编译器只校验 schema 本身，不校验实例字段）。
- 严格原则（对应需求「避免产出无法运行的流程」）：只要某步无法被完整参数化
  （如缺少选择器、网址推断不出），就**直接报错**并给出可操作的修正提示，绝不吐出半成品。
- NL→步骤的映射默认走确定性的 `KeywordInterpreter`（无外部依赖、可单测）；
  `PlanInterpreter` 是协议，后续可换成 LLM 后端而不动其余代码。
"""

from __future__ import annotations

from rpa_core.catalog import CommandCatalog, commands_root, load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.compiler.plan import ExecutionPlan
from rpa_core.model.workflow import Workflow

from .builder import build_workflow
from .errors import FlowGenerationError
from .generate import GeneratedFlow, generate_from_spec, generate_from_text
from .interpreter import InterpretedStep, KeywordInterpreter, PlanInterpreter
from .spec import FlowSpec, StepSpec
from .validate import validate_generated

# 与 CLI 的 `_compile` 保持同一组授权能力：经此校验生成的流程，
# 用 `rpa-core run` 时也能通过能力门禁（保证 generate ↔ run 一致）。
DEFAULT_GRANTED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "browser.read",
        "browser.control",
        "desktop.control",
        "process.start",
        "workspace.write",
    }
)

__all__ = [
    "FlowSpec",
    "StepSpec",
    "InterpretedStep",
    "PlanInterpreter",
    "KeywordInterpreter",
    "GeneratedFlow",
    "FlowGenerationError",
    "generate_from_spec",
    "generate_from_text",
    "build_workflow",
    "validate_generated",
    "load_catalog",
    "commands_root",
    "CommandCatalog",
    "WorkflowCompiler",
    "Workflow",
    "ExecutionPlan",
    "DEFAULT_GRANTED_CAPABILITIES",
]
