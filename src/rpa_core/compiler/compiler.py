import re
from typing import Any

from jsonschema import Draft202012Validator

from rpa_core.catalog import CommandCatalog
from rpa_core.model.command import ReplayPolicy
from rpa_core.model.workflow import (
    ActionNode,
    ForEachNode,
    IfNode,
    ReturnNode,
    SequenceNode,
    TryNode,
    Workflow,
    WorkflowNode,
)

from .plan import ExecutionPlan

_REFERENCE = re.compile(r"^\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}$")
_CONTROL_TYPES = {"sequence", "if", "forEach", "try", "return"}


class WorkflowCompileError(ValueError):
    pass


def _iter_references(value: Any):
    if isinstance(value, str):
        match = _REFERENCE.fullmatch(value)
        if match:
            yield match.group(1)
        elif value.startswith("${") and value.endswith("}"):
            raise WorkflowCompileError(f"Invalid reference syntax: {value}")
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_references(item)


def _collect_output_names(node: WorkflowNode) -> set[str]:
    """遍历整棵 workflow 树，收集所有声明的 output_name（供前向引用判定）。"""
    found: set[str] = set()

    def visit(n: WorkflowNode) -> None:
        if isinstance(n, ActionNode):
            if n.output_name:
                found.add(n.output_name)
        elif isinstance(n, SequenceNode):
            for child in n.children:
                visit(child)
        elif isinstance(n, IfNode):
            for child in n.then + n.otherwise:
                visit(child)
        elif isinstance(n, ForEachNode):
            for child in n.children:
                visit(child)
        elif isinstance(n, TryNode):
            for child in n.children + n.catch:
                visit(child)
        # ReturnNode 无子节点

    visit(node)
    return found


class WorkflowCompiler:
    def __init__(self, catalog: CommandCatalog):
        self.catalog = catalog

    def compile(self, workflow: Workflow, granted_capabilities: set[str]) -> ExecutionPlan:
        node_ids: set[str] = set()
        step_ids: set[str] = set()
        output_names: set[str] = set()  # 前序已声明的 output_name（全局顺序收集）
        all_output_names = _collect_output_names(workflow.root)
        required: set[str] = set()

        def validate_refs(value: Any, loop_vars: set[str], error_vars: set[str]) -> None:
            for reference in _iter_references(value):
                root, *parts = reference.split(".")
                if root == "inputs":
                    if not parts or parts[0] not in workflow.inputs:
                        raise WorkflowCompileError(f"Unknown workflow input reference: {reference}")
                elif root == "steps":
                    if len(parts) < 2 or parts[0] not in step_ids:
                        raise WorkflowCompileError(
                            f"Unknown or forward step reference: {reference}"
                        )
                elif root == "loop":
                    if not parts or parts[0] not in loop_vars:
                        raise WorkflowCompileError(f"Unknown loop reference: {reference}")
                elif root in output_names:
                    # ${var_name} 或 ${var_name.field} — 前序已声明的 output_name 变量
                    pass
                elif root in all_output_names:
                    # 变量在流程更靠后节点才声明 → 前向引用
                    raise WorkflowCompileError(
                        f"Forward variable reference (declared later): {reference}"
                    )
                elif root in error_vars:
                    # 词法可见的 catch error_var：${err} 或 ${err.field}
                    pass
                else:
                    # 作用域外的裸变量名 / error_var 越界 / 未声明变量 / 未知根
                    raise WorkflowCompileError(f"Unsupported reference root: {reference}")

        def walk(
            node: WorkflowNode, loop_vars: set[str], error_vars: set[str] | None = None
        ) -> None:
            error_vars = error_vars or set()
            if node.id in node_ids:
                raise WorkflowCompileError(f"Duplicate node id: {node.id}")
            node_ids.add(node.id)

            if isinstance(node, ActionNode):
                if node.command in _CONTROL_TYPES:
                    raise WorkflowCompileError(f"Control flow cannot be a command: {node.command}")
                manifest = self.catalog.get(node.command)
                if manifest is None:
                    raise WorkflowCompileError(f"Unknown command: {node.command}")
                validate_refs(node.with_, loop_vars, error_vars)
                if node.retry_count > 0 and manifest.effect.replay == ReplayPolicy.UNSAFE:
                    raise WorkflowCompileError(
                        f"Unsafe replay command cannot be retried: {manifest.id}"
                    )
                Draft202012Validator(manifest.input_schema).check_schema(manifest.input_schema)
                required.update(manifest.capabilities)
                step_ids.add(node.id)
                if node.output_name:
                    output_names.add(node.output_name)
                return

            if isinstance(node, SequenceNode):
                for child in node.children:
                    walk(child, loop_vars, error_vars)
            elif isinstance(node, IfNode):
                validate_refs(node.condition.model_dump(), loop_vars, error_vars)
                for child in node.then:
                    walk(child, loop_vars, error_vars)
                for child in node.otherwise:
                    walk(child, loop_vars, error_vars)
            elif isinstance(node, ForEachNode):
                validate_refs(node.items, loop_vars, error_vars)
                child_vars = loop_vars | {node.item_var}
                for child in node.children:
                    walk(child, child_vars, error_vars)
            elif isinstance(node, TryNode):
                for child in node.children:
                    walk(child, loop_vars, error_vars)
                catch_error_vars = error_vars | {node.error_var}
                for child in node.catch:
                    walk(child, loop_vars, catch_error_vars)
            elif isinstance(node, ReturnNode):
                validate_refs(node.value, loop_vars, error_vars)

        walk(workflow.root, set(), set())
        denied = required - granted_capabilities
        if denied:
            raise WorkflowCompileError(f"Missing capabilities: {', '.join(sorted(denied))}")
        return ExecutionPlan(
            workflow=workflow,
            catalog_digest=self.catalog.digest,
            required_capabilities=frozenset(required),
        )
