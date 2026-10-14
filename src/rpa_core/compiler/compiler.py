import re
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from rpa_core.catalog import CommandCatalog
from rpa_core.model.command import ReplayPolicy
from rpa_core.model.inputs import InputDeclarationError, validate_declaration
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

# 内置作用域根名：用户别名不得占用，否则 resolver 的 variables 优先查找会静默遮蔽它们。
_RESERVED_ALIAS_ROOTS = {"inputs", "steps", "loop"}


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


def _iter_action_nodes(node: WorkflowNode):
    """按流程顺序遍历所有 ActionNode（用于别名声明顺序与查重）。"""
    if isinstance(node, ActionNode):
        yield node
        return
    if isinstance(node, SequenceNode):
        for child in node.children:
            yield from _iter_action_nodes(child)
    elif isinstance(node, IfNode):
        for child in node.then + node.otherwise:
            yield from _iter_action_nodes(child)
    elif isinstance(node, ForEachNode):
        for child in node.children:
            yield from _iter_action_nodes(child)
    elif isinstance(node, TryNode):
        for child in node.children + node.catch:
            yield from _iter_action_nodes(child)
    # ReturnNode 无子节点


def _validate_alias_declarations(root: WorkflowNode, catalog: CommandCatalog) -> None:
    """校验别名声明本身：保留名占用与重复声明。

    别名存储在扁平作用域 scopes["variables"] 中，后写覆盖前写，因此：
    - 别名占用内置作用域根名（inputs/steps/loop）会静默遮蔽整个作用域；
    - 两个节点声明同名别名会导致先声明者被静默覆盖。
    两者都必须在编译期拦截，避免"能跑但结果错"。

    例外：变量写入命令（manifest x-var-write，如 data.setVar）语义就是"赋值"，
    允许覆盖同名变量（定义 → 修改 → 再读），不参与重复拦截。
    """
    seen: dict[str, str] = {}  # alias -> 首次声明的 node id
    for node in _iter_action_nodes(root):
        if _is_var_write_node(node, catalog):
            # 赋值命令允许同名覆盖；但其 varName 字面量若占用保留名仍属危险。
            manifest = catalog.get(node.command)
            name_field = (manifest.x_var_write or {}).get("field") if manifest else None
            target = node.with_.get(name_field) if name_field else None
            if isinstance(target, str) and target in _RESERVED_ALIAS_ROOTS:
                raise WorkflowCompileError(
                    f"Reserved alias name: {target!r} is a built-in scope root "
                    f"(assigned by node {node.id})"
                )
            continue
        for alias in (node.output_aliases or {}).values():
            if alias in _RESERVED_ALIAS_ROOTS:
                raise WorkflowCompileError(
                    f"Reserved alias name: {alias!r} is a built-in scope root "
                    f"(declared by node {node.id})"
                )
            previous = seen.get(alias)
            if previous is not None:
                raise WorkflowCompileError(
                    f"Duplicate alias: {alias!r} declared by node {previous} "
                    f"and node {node.id}"
                )
            seen[alias] = node.id


def _is_var_write_node(node: ActionNode, catalog: CommandCatalog) -> bool:
    """判断节点是否为变量写入命令（manifest 声明 x-var-write）。"""
    manifest = catalog.get(node.command)
    return bool(manifest and manifest.x_var_write)


def _declared_names(node: ActionNode, catalog: CommandCatalog) -> list[str]:
    """**单个节点自己**声明的变量名，按声明顺序：output_aliases + x-var-write 目标。

    动态变量名（varName 是引用）静态无法确定，与前向引用判定同口径地跳过。
    """
    names = [alias for alias in (node.output_aliases or {}).values() if alias]
    manifest = catalog.get(node.command)
    var_write = manifest.x_var_write if manifest else None
    if var_write and var_write.get("field"):
        target = node.with_.get(var_write["field"])
        if isinstance(target, str) and target and not target.startswith("${"):
            names.append(target)
    return names


@dataclass(frozen=True)
class VisibleScope:
    """某个节点上「可引用」的作用域（用户变量 + 词法可见的循环/错误变量）。

    ``variables`` 是**严格在该节点之前**声明的用户变量名（声明顺序、去重）；
    它**不含**该节点自身声明的名字——这正是编译器的口径：``walk()`` 先
    ``validate_refs(node.with_, …)``、之后才登记本节点的别名。
    """

    variables: tuple[str, ...]
    loop_vars: tuple[str, ...]
    error_vars: tuple[str, ...]


def visible_scope(root: WorkflowNode, catalog: CommandCatalog, node_id: str) -> VisibleScope | None:
    """算出 ``node_id`` 这个节点上可见的作用域；节点不存在时返回 ``None``。

    遍历顺序必须与 :meth:`WorkflowCompiler.compile` 的 ``walk()`` **逐条对齐**
    （sequence 顺序 / if 的 then→else / forEach 的 children / try 的 body→catch），
    否则菜单会给出一份「编译器并不这么认为」的可见集。这条约束由
    ``tests/contract/test_variable_scope_visibility.py`` 用编译器当**裁判**逐格钉住
    （能过 = 可见，不能过 = 不可见），实现一旦漂移即红。Web 侧另有一份 JS 实现，
    由 ``scripts/check_scope_visibility.mjs`` 拿**同一张用例表**（
    ``tests/contract/data/scope_visibility_cases.json``）两头对齐。

    ★ 已知的编译器口径（不是本函数的缺陷，是照抄）：then 里声明的变量在 **else** 里
    可见、try 体里声明的在 **catch** 里可见。菜单与它**同口径**，因为维护者
    2026-10-14 拍板「菜单 ⊆ 编译通过，两端口径必须一致」。
    """
    seen: list[str] = []
    hit: VisibleScope | None = None

    def visit(node: WorkflowNode, loop_vars: set[str], error_vars: set[str]) -> None:
        nonlocal hit
        if hit is not None:
            return
        if node.id == node_id:
            ordered = list(dict.fromkeys(seen))
            hit = VisibleScope(
                tuple(ordered),
                tuple(dict.fromkeys(loop_vars)),
                tuple(dict.fromkeys(error_vars)),
            )
            return
        if isinstance(node, ActionNode):
            seen.extend(_declared_names(node, catalog))
            return
        if isinstance(node, SequenceNode):
            for child in node.children:
                visit(child, loop_vars, error_vars)
        elif isinstance(node, IfNode):
            for child in node.then:
                visit(child, loop_vars, error_vars)
            for child in node.otherwise:
                visit(child, loop_vars, error_vars)
        elif isinstance(node, ForEachNode):
            child_vars = loop_vars | {node.item_var}
            for child in node.children:
                visit(child, child_vars, error_vars)
        elif isinstance(node, TryNode):
            for child in node.children:
                visit(child, loop_vars, error_vars)
            catch_vars = error_vars | {node.error_var}
            for child in node.catch:
                visit(child, loop_vars, catch_vars)
        # ReturnNode 无子节点

    visit(root, set(), set())
    return hit


def _collect_output_aliases(node: WorkflowNode, catalog: CommandCatalog) -> set[str]:
    """遍历整棵 workflow 树，收集所有声明的变量名（供前向引用判定）。

    包含两类：output_aliases 的别名值，以及变量写入命令（x-var-write）中
    varName 为字面量时声明的变量名。动态变量名（varName 为引用）无法静态确定。
    """
    found: set[str] = set()

    def visit(n: WorkflowNode) -> None:
        if isinstance(n, ActionNode):
            found.update(_declared_names(n, catalog))
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
        output_aliases: set[str] = set()  # 前序已声明的 output_aliases（全局顺序收集）
        all_output_aliases = set(_collect_output_aliases(workflow.root, self.catalog))
        required: set[str] = set()

        # 别名声明本身先校验（保留名 / 重复），失败即拒绝整份 workflow。
        _validate_alias_declarations(workflow.root, self.catalog)

        # 流程 inputs 声明本身也要校验（M26 S1）：名字必须能被 ${inputs.<名>} 引用，
        # 且不得占用内置作用域根名——否则声明在册但永远引用不上，属「能跑但结果错」。
        # 放在引用校验之前：先保证声明合法，再看引用是否落在声明里。
        try:
            validate_declaration(workflow.inputs)
        except InputDeclarationError as exc:
            raise WorkflowCompileError(f"Invalid workflow inputs declaration: {exc}") from exc

        def validate_refs(
            value: Any,
            loop_vars: set[str],
            error_vars: set[str],
            own_names: set[str],
        ) -> None:
            """校验一处引用。``own_names`` = 当前节点自己声明的变量名。

            判定次序（改动前请先读 `visible_scope` 的说明）：
            已可见 → **自身声明** → 更靠后声明 → 词法可见的 error_var → 不可用。
            「自身声明」必须排在「更靠后声明」**之前**，否则自引用会报成
            「declared later」（文案误导）；又必须排在「已可见」**之后**，
            否则 ``v = ${v}``（读自己写入目标的旧值）这类合法写法会被误判。
            """
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
                elif root in output_aliases:
                    # ${alias} 或 ${alias.field} — 前序已声明的 output_alias 变量
                    pass
                elif root in own_names:
                    # 变量就由**当前节点自己**声明 → 引用它必然取不到（赋值还没发生）
                    raise WorkflowCompileError(
                        f"Self variable reference (declared by this node): {reference}"
                    )
                elif root in all_output_aliases:
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
                validate_refs(
                    node.with_,
                    loop_vars,
                    error_vars,
                    set(_declared_names(node, self.catalog)),
                )
                if node.retry_count > 0 and manifest.effect.replay == ReplayPolicy.UNSAFE:
                    raise WorkflowCompileError(
                        f"Unsafe replay command cannot be retried: {manifest.id}"
                    )
                Draft202012Validator(manifest.input_schema).check_schema(manifest.input_schema)
                required.update(manifest.capabilities)
                step_ids.add(node.id)
                # 本节点声明的变量：output_aliases + 变量写入命令的字面量 varName
                # （varName 是引用时静态无法确定，跳过）。**唯一来源是
                # `_declared_names`**——`validate_refs` 的「自身声明」判定与
                # `visible_scope` 的可见集都读它，三处不得各写一份。
                output_aliases.update(_declared_names(node, self.catalog))
                return

            if isinstance(node, SequenceNode):
                for child in node.children:
                    walk(child, loop_vars, error_vars)
            elif isinstance(node, IfNode):
                validate_refs(node.condition.model_dump(), loop_vars, error_vars, set())
                for child in node.then:
                    walk(child, loop_vars, error_vars)
                for child in node.otherwise:
                    walk(child, loop_vars, error_vars)
            elif isinstance(node, ForEachNode):
                validate_refs(node.items, loop_vars, error_vars, set())
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
                validate_refs(node.value, loop_vars, error_vars, set())

        walk(workflow.root, set(), set())
        denied = required - granted_capabilities
        if denied:
            raise WorkflowCompileError(f"Missing capabilities: {', '.join(sorted(denied))}")
        return ExecutionPlan(
            workflow=workflow,
            catalog_digest=self.catalog.digest,
            required_capabilities=frozenset(required),
        )
