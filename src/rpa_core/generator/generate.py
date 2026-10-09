"""生成门面：把「描述」变成「可被加载执行的流程文件」。

入口：
- `generate_from_text(desc, ...)`：自然语言描述 → 流程（内部先按分隔符拆步，再逐步解释）。
- `generate_from_spec(spec, ...)`：结构化 FlowSpec → 流程。
- `GeneratedFlow`：产物，含 `Workflow` 模型、`ExecutionPlan`、警告列表，可 `write(path)` 落盘。

所有入口都强制走 `validate_generated` 两道闸门，未通过即抛 `FlowGenerationError`，
保证落盘/返回的一定是可被 `rpa-core validate/run/gui` 直接消费的文件（或模型）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rpa_core.catalog import CommandCatalog, commands_root, load_catalog
from rpa_core.compiler.plan import ExecutionPlan
from rpa_core.model.workflow import Workflow

from .builder import build_workflow
from .errors import FlowGenerationError
from .interpreter import KeywordInterpreter, PlanInterpreter
from .spec import FlowSpec, StepSpec
from .validate import validate_generated

# 拆步分隔符：换行 / 中文句号分号 / 连接词（把「A 然后 B」切成两步）。
_STEP_SEP = re.compile(r"\n+|[；;。]|然后|接着|随后|并且|而且")


def split_steps(text: str) -> list[str]:
    """把一段自然语言描述拆成有序的操作步骤（句子）。"""
    out: list[str] = []
    for part in _STEP_SEP.split(text):
        piece = part.strip(" ，,、\t")
        if piece:
            out.append(piece)
    return out


@dataclass
class GeneratedFlow:
    """一次生成的产物。"""

    workflow: Workflow
    plan: ExecutionPlan
    warnings: list[str]
    spec: FlowSpec

    def model_dump_json(self, **kwargs) -> str:
        return self.workflow.model_dump_json(**kwargs)

    def write(self, path: str | Path) -> Path:
        """落盘为 `workflow.json`（兼容项目既有加载方式）。返回实际写入路径。"""
        path = Path(path)
        if path.is_dir():
            path = path / "workflow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.workflow.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return path


def generate_from_spec(
    spec: FlowSpec,
    *,
    catalog: CommandCatalog | None = None,
    granted_capabilities: set[str] | None = None,
    interpreter: PlanInterpreter | None = None,
) -> GeneratedFlow:
    catalog = catalog or load_catalog(commands_root())
    interpreter = interpreter or KeywordInterpreter()
    granted = (
        granted_capabilities
        if granted_capabilities is not None
        else _default_caps()
    )
    warnings: list[str] = []
    workflow = build_workflow(spec, catalog=catalog, interpreter=interpreter, warnings=warnings)
    plan, vwarn = validate_generated(workflow, catalog, granted)
    warnings.extend(vwarn)
    return GeneratedFlow(workflow=workflow, plan=plan, warnings=warnings, spec=spec)


def generate_from_text(
    text: str,
    *,
    name: str | None = None,
    id: str | None = None,
    inputs: dict | None = None,
    catalog: CommandCatalog | None = None,
    granted_capabilities: set[str] | None = None,
    interpreter: PlanInterpreter | None = None,
) -> GeneratedFlow:
    chunks = split_steps(text)
    if not chunks:
        raise FlowGenerationError("描述为空或无法拆分为任何操作步骤")
    steps = [StepSpec(action=chunk) for chunk in chunks]
    spec = FlowSpec(
        name=name or "自动生成的流程",
        id=id or "generated",
        inputs=inputs or {},
        steps=steps,
    )
    return generate_from_spec(
        spec,
        catalog=catalog,
        granted_capabilities=granted_capabilities,
        interpreter=interpreter,
    )


def _default_caps() -> set[str]:
    # 延迟导入，避免循环依赖；与 __init__ 的 DEFAULT_GRANTED_CAPABILITIES 同源。
    from . import DEFAULT_GRANTED_CAPABILITIES

    return set(DEFAULT_GRANTED_CAPABILITIES)
