"""生成结果的合法性校验：两道闸门，确保产物可被项目直接加载执行。

闸门 1（结构/引用/能力/别名/节点 id）：直接复用真实 `WorkflowCompiler`。
它已经实现：未知命令、重复 id、非法引用（含前向引用）、能力缺口、保留名占用、
重复别名、不安全重放命令不可重试等全部规则——生成的流程必须能过这一关。

闸门 2（参数实例级）：编译器只校验每个命令 manifest 的 `input_schema` 本身是否合法，
**不校验**某节点 `with` 实例是否满足 schema。这里补上实例级校验：
用 `jsonschema` 把每个 action 节点的 `with` 拿去 validate，捕获
必填缺失、类型不符、枚举越界、`additionalProperties:false` 外的多余字段等。
"""

from __future__ import annotations

from jsonschema import Draft202012Validator

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler import WorkflowCompileError
from rpa_core.compiler.compiler import WorkflowCompiler
from rpa_core.compiler.plan import ExecutionPlan
from rpa_core.model.workflow import Workflow

from .builder import _iter_action_nodes
from .errors import FlowGenerationError


def validate_generated(
    workflow: Workflow,
    catalog: CommandCatalog,
    granted_capabilities: set[str],
) -> tuple[ExecutionPlan, list[str]]:
    """校验生成的流程；通过则返回 (执行计划, 警告列表)，否则抛 `FlowGenerationError`。"""
    warnings: list[str] = []

    # 闸门 1：复用真实编译器（引用/能力/别名/节点 id 等全量规则）
    try:
        plan = WorkflowCompiler(catalog).compile(workflow, granted_capabilities)
    except WorkflowCompileError as exc:
        raise FlowGenerationError(f"流程编译未通过：{exc}") from exc

    # 闸门 2：逐节点参数实例级校验
    for node in _iter_action_nodes(workflow.root):
        manifest = catalog.get(node.command)
        if manifest is None:
            # 编译器已能拦住，这里冗余兜底
            raise FlowGenerationError(f"未知命令：{node.command}（节点 {node.id}）")
        schema = manifest.input_schema or {}
        validator = Draft202012Validator(schema)
        errors = sorted(
            (list(e.path), e.message) for e in validator.iter_errors(node.with_)
        )
        if errors:
            detail = "；".join(
                f"{'/'.join(map(str, path)) or '<root>'}: {msg}" for path, msg in errors
            )
            raise FlowGenerationError(
                f"节点 {node.id}（{node.command}）参数不满足 input_schema：{detail}",
                details=[f"{'/'.join(map(str, p)) or '<root>'}: {m}" for p, m in errors],
            )

    return plan, warnings
