"""把 FlowSpec / 解释结果组装成合法的 `Workflow`（pydantic 模型）。

职责：
- 为每个步骤分配唯一节点 id（`step_1`、`step_2`…），天然避免重复 id。
- 浏览器会话自动串联：navigate 的主输出 sessionId 挂到 `page_<n>` 变量，
  其后同流程内的浏览器命令若未显式给 sessionId，自动注入 `${page_<n>}`。
- 截图路径自动补全（避免每次都让用户手写）。
- id / 变量名规范化，过 `Workflow` 模型的字段约束。
"""

from __future__ import annotations

import re

from rpa_core.catalog import CommandCatalog
from rpa_core.model.workflow import ActionNode, SequenceNode, Workflow

from .errors import FlowGenerationError
from .interpreter import InterpretedStep, PlanInterpreter
from .spec import FlowSpec, StepSpec

_FLOW_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_flow_id(raw: str) -> str:
    if not raw:
        return "generated"
    # 非 ASCII / 非法字符 → 下划线；首字符非字母 → 前缀 flow_
    cleaned = _UNSAFE.sub("_", raw).strip("_")
    if not cleaned:
        return "generated"
    if not cleaned[0].isalpha():
        cleaned = f"flow_{cleaned}"
    if not _FLOW_ID.match(cleaned):
        cleaned = cleaned[:64]
    return cleaned or "generated"


def _alias_map(manifest, alias_name: str) -> dict[str, str]:
    """把用户给的变量名映射到命令的「首个输出字段」。

    命令 manifest 的输出字段即运行时可引用的变量来源；取 required 第一个，
    否则取 properties 第一个。拿不到输出字段则回落为空（不挂别名）。
    """
    out = (manifest.output_schema or {})
    required = out.get("required") or []
    props = out.get("properties") or {}
    field = (required[0] if required else next(iter(props), None))
    if not field:
        return {}
    return {field: alias_name}


def _iter_action_nodes(node):
    from rpa_core.model.workflow import ActionNode as _Act
    from rpa_core.model.workflow import SequenceNode as _Seq

    if isinstance(node, _Act):
        yield node
    elif isinstance(node, _Seq):
        for child in node.children:
            yield from _iter_action_nodes(child)


def build_workflow(
    spec: FlowSpec,
    *,
    catalog: CommandCatalog,
    interpreter: PlanInterpreter,
    warnings: list[str],
    screenshot_dir: str = "outputs",
) -> Workflow:
    if not spec.steps:
        raise FlowGenerationError("流程至少需要一个步骤")

    nodes: list[ActionNode] = []
    nav_alias: str | None = None
    nav_index = 0
    shot_index = 0

    for i, step in enumerate(spec.steps, start=1):
        command, with_, out_alias, step_warnings = _resolve_step(
            step, i, catalog=catalog, interpreter=interpreter
        )
        warnings.extend(step_warnings)

        manifest = catalog.get(command)
        if manifest is None:
            raise FlowGenerationError(f"步骤 {i} 引用了未知命令：{command}", step_index=i)

        # 浏览器会话自动串联
        if command == "browser.navigate":
            nav_index += 1
            nav_alias = f"page_{nav_index}"
            out_alias = {"sessionId": nav_alias}
        elif out_alias:
            out_alias = _alias_map(manifest, out_alias)

        # 截图路径自动补全
        if command == "browser.screenshot":
            props = (manifest.input_schema or {}).get("properties") or {}
            if props.get("savePath") and with_.get("savePath") == "__AUTO_SCREENSHOT__":
                shot_index += 1
                with_["savePath"] = f"{screenshot_dir}/screenshot_{shot_index}.png"

        # 浏览器页面命令：未显式给 sessionId 时，注入最近的会话变量
        if manifest.executor == "browser.playwright":
            props = (manifest.input_schema or {}).get("properties") or {}
            if "sessionId" in props and "sessionId" not in with_:
                if nav_alias:
                    with_["sessionId"] = f"${{{nav_alias}}}"
                else:
                    warnings.append(
                        f"步骤 {i}（{command}）在「打开网页」之前执行，"
                        f"缺少会话 sessionId，运行可能失败"
                    )

        node = ActionNode.model_validate(
            {
                "id": f"step_{i}",
                "command": command,
                "with": with_,
                "output_aliases": out_alias or {},
            }
        )
        nodes.append(node)

    root = SequenceNode(id="root", children=list(nodes))
    return Workflow(
        schema_version="1.0",
        id=_sanitize_flow_id(spec.id),
        name=spec.name,
        inputs=dict(spec.inputs),
        timeout_seconds=spec.timeout_seconds,
        root=root,
    )


def _resolve_step(
    step: StepSpec,
    index: int,
    *,
    catalog: CommandCatalog,
    interpreter: PlanInterpreter,
):
    """单步解析：返回 (command, with_, output_alias, warnings)。"""
    if step.command:
        if not step.action:
            pass  # 显式命令优先
        return step.command, dict(step.with_), step.output_alias, []

    if not step.action:
        raise FlowGenerationError(
            f"步骤 {index} 既未提供 command 也未提供 action 描述", step_index=index
        )

    try:
        interpreted: InterpretedStep | None = interpreter.interpret(step.action, catalog=catalog)
    except FlowGenerationError:
        raise

    if interpreted is None:
        raise FlowGenerationError(
            f"无法识别步骤 {index} 的操作：「{step.action}」", step_index=index
        )
    return (
        interpreted.command,
        dict(interpreted.with_),
        interpreted.output_alias,
        list(interpreted.warnings),
    )
