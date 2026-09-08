from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")


class ActionNode(NodeBase):
    type: Literal["action"] = "action"
    command: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    output_name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_]\w*$",
        description="用户可读的输出变量名，如 web_page1",
    )
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    retry_count: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.1, ge=0, le=60)
    retry_backoff_max_seconds: float = Field(default=5, ge=0, le=300)


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
