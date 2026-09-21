"""流程 `inputs` 声明的规范化与校验（M26 S1）。

现状（本文件写之前的真实形状）：`Workflow.inputs` 是 `dict[str, Any]`，语义是
**`{名称: 默认值}` 的扁平映射**——没有类型、没有必填、没有描述三个字段。
GUI 的「运行参数」对话框（`gui/app.py:RunParamsDialog`）直接 `for name, default in inputs.items()`
渲染一行 `QLineEdit`，把用户填的文本按 JSON 解析；`orchestrator` 做
`{"inputs": {**plan.workflow.inputs, **inputs}}` 合并进 `scopes.inputs`；编译期
`validate_refs` 检查 `inputs.<名>` 是否在声明里。

M26 的目标是让这份声明在 GUI 里可增删改。本模块只做**规范化 + 校验**，不引入第二份声明格式：

- **形状兼容是硬约束**：`{名: 默认值}` 原样保留。所谓「规范化」只是把同一个 dict 变成**有序、
  可编辑的条目列表**（`name` + `json_default`），编辑完再由 `declaration_from_entries()` 还原成
  原形状。**不新增 `type`/`required`/`description` 字段**——那会改变既有文件的语义，
  且 `Workflow.inputs` 与 `inputs.<名>` 的引用文法都要跟着改。任务单里「若现形状无该字段，
  则本切片只做『名称 + 默认值』两列」的那条备选，就是本文件的落点。
- **默认值是任意 JSON**：`Workflow.inputs: dict[str, Any]` 允许 null / 数字 / 列表 / 对象。
  所以编辑器侧用 **JSON 文本**承载（与 `RunParamsDialog` 同口径），本模块负责往返与合法性检查。

## 为什么名称规则要比 `_REFERENCE` 更严

编译器解析引用的正则是 `^\\$\\{([A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*)\\}$`，即**点号是路径分隔符**。
所以名字里若含 `.`，`${inputs.a.b}` 会被解析成「inputs 下的 `a`，再取字段 `b`」，
而声明若叫 `a.b` 则永远匹配不上——**声明与引用两条文法不一致**。因此本模块要求
**`[A-Za-z_]\\w*`（不含点号）**，比 `Workflow.id` 的 `^[A-Za-z][A-Za-z0-9_.-]*$` 严。
这是刻意的：声明名是要被 `${...}` 引用的标识符，不是文件名。

## 保留名

`inputs` 名字不得占用内置作用域根名（`inputs` / `steps` / `loop`，与
`compiler._RESERVED_ALIAS_ROOTS` 同口径）。否则 `${inputs.inputs}` 这类引用会产生歧义，
且与 `orchestrator` 的 `scopes` 结构冲突。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# 与 compiler._REFERENCE 的标识符段同口径：字母/下划线开头，后接字母数字下划线。
# **不含点号**——点号是引用路径的分隔符，见模块 docstring。
INPUT_NAME_PATTERN = re.compile(r"^[A-Za-z_]\w*$")

# 与 compiler._RESERVED_ALIAS_ROOTS 同口径（内置作用域根名，占用会产生歧义/遮蔽）
RESERVED_INPUT_NAMES = frozenset({"inputs", "steps", "loop"})

# 名称长度上限：仅用于挡住荒谬输入（避免 GUI 里造出不可用的引用），非格式要求
MAX_INPUT_NAME_LENGTH = 64


class InputDeclarationError(ValueError):
    """`inputs` 声明不合法（名称格式 / 保留名 / 重复 / 默认值不可序列化）。"""


@dataclass(frozen=True)
class InputEntry:
    """一条可编辑的输入声明：名称 + 默认值的 JSON 文本。

    `json_text` 用文本而非 Python 值承载，是为了让 GUI 表格能「正在输入的非法 JSON」
    与「合法空值」区分开：空文本 = 默认值为 `null`（与 `RunParamsDialog` 的
    「空 = 沿用默认」不同——这里编辑的是**声明本身**，声明必须落盘）。
    """

    name: str
    json_text: str

    @property
    def default_value(self) -> Any:
        """解析后的默认值；文本为空视作 `null`。非法 JSON 抛 `InputDeclarationError`。"""
        text = self.json_text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputDeclarationError(
                f"输入 {self.name} 的默认值不是合法 JSON：{exc.msg}"
            ) from exc

    def to_json_text(self) -> str:
        """规范化文本：解析（借机校验）后按紧凑格式回写，去掉多余空白与键序抖动。

        `sort_keys=True` 让「对象默认值」的键序稳定——否则同一份声明两次保存会产生
        不同的字节，GUI 的脏标记与 `git diff` 都会被无意义的键序抖动污染。
        """
        return json.dumps(self.default_value, ensure_ascii=False, sort_keys=True)


def validate_input_name(name: str) -> None:
    """校验单个输入名；不合法直接抛 `InputDeclarationError`。"""
    if not name:
        raise InputDeclarationError("输入名不能为空")
    if len(name) > MAX_INPUT_NAME_LENGTH:
        raise InputDeclarationError(
            f"输入名过长（{len(name)} > {MAX_INPUT_NAME_LENGTH}）：{name}"
        )
    if not INPUT_NAME_PATTERN.match(name):
        raise InputDeclarationError(
            f"输入名 {name!r} 不是合法标识符：只允许字母/下划线开头，后接字母数字下划线"
            "（不含点号——点号是 ${...} 引用的路径分隔符）"
        )
    if name in RESERVED_INPUT_NAMES:
        raise InputDeclarationError(
            f"输入名 {name!r} 是内置作用域根名（{', '.join(sorted(RESERVED_INPUT_NAMES))}），"
            "占用会使 ${...} 引用产生歧义"
        )


def entries_from_declaration(declaration: dict[str, Any] | None) -> list[InputEntry]:
    """`{名: 默认值}` → 有序条目列表（按名称排序，保证界面与保存结果稳定）。

    只做**形状读取**，不做校验——读取既有文件时不该因为历史声明不合法就打不开；
    校验发生在保存前（`validate_declaration`）。
    """
    if not declaration:
        return []
    out: list[InputEntry] = []
    for name in sorted(declaration):
        default = declaration[name]
        text = "" if default is None else json.dumps(default, ensure_ascii=False, sort_keys=True)
        out.append(InputEntry(name=str(name), json_text=text))
    return out


def declaration_from_entries(entries: list[InputEntry]) -> dict[str, Any]:
    """有序条目列表 → `{名: 默认值}`（保存回写用）。校验不通过则抛异常、不产出半成品。"""
    validate_entries(entries)
    return {entry.name: entry.default_value for entry in entries}


def validate_entries(entries: list[InputEntry]) -> None:
    """校验整份声明：逐项名称合法 + 名称不重复 + 默认值可解析。

    一次性收集**全部**问题再抛，而不是遇到第一个就退出——GUI 上表格可能有多个错，
    逐条报（而不是让用户改一个、保存一次、再报下一个）。
    """
    problems: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            validate_input_name(entry.name)
        except InputDeclarationError as exc:
            problems.append(str(exc))
            continue
        if entry.name in seen:
            problems.append(f"输入名重复：{entry.name!r}")
        seen.add(entry.name)
        try:
            _ = entry.default_value  # 借读取触发 JSON 合法性检查
        except InputDeclarationError as exc:
            problems.append(str(exc))
    if problems:
        raise InputDeclarationError("；".join(problems))


def validate_declaration(declaration: dict[str, Any] | None) -> None:
    """校验现有的 `{名: 默认值}` 声明（读文件/编译前的兜底入口）。"""
    if not declaration:
        return
    entries: list[InputEntry] = []
    for name, default in declaration.items():
        text = "" if default is None else json.dumps(default, ensure_ascii=False, sort_keys=True)
        entries.append(InputEntry(name=str(name), json_text=text))
    # 名称重复在 dict 形状下不可能出现，但名称合法性与默认值可序列化仍要查
    problems: list[str] = []
    for entry in entries:
        try:
            validate_input_name(entry.name)
        except InputDeclarationError as exc:
            problems.append(str(exc))
    if problems:
        raise InputDeclarationError("；".join(problems))


def referenced_input_names(reference: str) -> str | None:
    """从形如 `inputs.<名>` 的引用取 `<名>`；不是 inputs 引用则返回 None。

    供「删除声明后找出残留引用」用（M26 S3）：这里只做**提取**，
    是否算「残留」由调用方对照当前声明判断。
    """
    parts = reference.split(".")
    if len(parts) < 2 or parts[0] != "inputs":
        return None
    return parts[1]
