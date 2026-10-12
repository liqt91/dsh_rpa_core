from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")


class ActionNode(NodeBase):
    type: Literal["action"] = "action"
    command: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    output_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="输出字段别名，如 {\"sessionId\": \"web\"}，后续可用 ${web} 引用",
    )
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    retry_count: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.1, ge=0, le=60)
    retry_backoff_max_seconds: float = Field(default=5, ge=0, le=300)
    expr_modes: dict[str, str] | None = Field(
        default=None,
        alias="_exprModes",
        description="字段表达式模式：fx=变量引用, python=Python表达式",
    )
    element_refs: dict[str, str] | None = Field(
        default=None,
        alias="elementRefs",
        description=(
            "元素引用（M46/B1 引用模型，与影刀同构）：参数键 → 元素库元素名。"
            "M52 S6 起为**唯一真值源**：被引用的参数**不再在 `with` 里留值快照**，"
            "运行期从流程 elements/ 目录解析最新定位值；解析不到即 `ELEMENT_REF_MISSING` 硬失败"
        ),
    )

    @model_validator(mode="after")
    def _reject_snapshot_alongside_element_ref(self) -> "ActionNode":
        """M52 S6 硬切：被元素引用的参数**不允许**同时带值快照。

        早先每个元素参数是**双写**（值进 `with`、引用进 `elementRefs`，快照只为
        「元素缺失时回落」而留）。两份真值就是「选了元素、selector 却没落进节点」
        这类事故的土壤，故按维护者 2026-10-12 拍板去掉快照：**引用就是引用**。
        早于本切点的流程文件同时带两者，这里**直接报错**而不静默迁移（维护者选「硬切」）。
        """
        refs = self.element_refs or {}
        clashes = sorted(key for key in refs if key in self.with_)
        if clashes:
            names = "、".join(f"`{key}`" for key in clashes)
            raise ValueError(
                f"参数 {names} 同时有值快照与元素引用（旧版双写数据）；"
                "元素引用即唯一真值——请删掉该参数的值，或重新插入元素"
            )
        return self


class SequenceNode(NodeBase):
    type: Literal["sequence"] = "sequence"
    children: list["WorkflowNode"]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "truthy"]
    left: Any
    right: Any = None


class IfNode(NodeBase):
    type: Literal["if"] = "if"
    condition: Condition
    then: list["WorkflowNode"]
    otherwise: list["WorkflowNode"] = Field(default_factory=list, alias="else")


class ForEachNode(NodeBase):
    type: Literal["forEach"] = "forEach"
    items: Any
    item_var: str = Field(default="item", pattern=r"^[A-Za-z_]\w*$")
    children: list["WorkflowNode"]


class TryNode(NodeBase):
    type: Literal["try"] = "try"
    children: list["WorkflowNode"]
    catch: list["WorkflowNode"] = Field(default_factory=list)
    error_var: str = Field(default="error", pattern=r"^[A-Za-z_]\w*$")


class ReturnNode(NodeBase):
    type: Literal["return"] = "return"
    value: Any = None


type WorkflowNode = Annotated[
    ActionNode | SequenceNode | IfNode | ForEachNode | TryNode | ReturnNode,
    Field(discriminator="type"),
]


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=3600, gt=0, le=86400)
    root: WorkflowNode


SequenceNode.model_rebuild()
IfNode.model_rebuild()
ForEachNode.model_rebuild()
TryNode.model_rebuild()
Workflow.model_rebuild()
