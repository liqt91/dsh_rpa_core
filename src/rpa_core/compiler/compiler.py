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


class WorkflowCompiler:
    def __init__(self, catalog: CommandCatalog):
        self.catalog = catalog

    def compile(self, workflow: Workflow, granted_capabilities: set[str]) -> ExecutionPlan:
        node_ids: set[str] = set()
        step_ids: set[str] = set()
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
                elif root not in error_vars:
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
