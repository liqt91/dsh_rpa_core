"""生成能力的输入契约：FlowSpec / StepSpec。

两种写法都支持，且都最终落到同一份 `Workflow`：
- 自然语言步：`StepSpec(action="打开 https://example.com")`
- 显式命令步：`StepSpec(command="browser.navigate", with_={"url": "..."})`

`with_` 用别名 "with"（与 `ActionNode`、命令 manifest 的字段名一致），序列化时即输出 `with`。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# 与 model/workflow.py 的约束同源：流程 id 形如 `example_search`，节点 id 形如 `step_1`。
_FLOW_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# 仅做形状约束，具体 id 合法性由 build 阶段再用 workflow 模型的校验兜底。


class StepSpec(BaseModel):
    """单个操作步骤：自然语言描述与显式命令二选一（也可都给，命令优先）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command: str | None = None
    with_: dict = Field(default_factory=dict, alias="with")
    action: str | None = None
    # 把该步「主输出字段」挂到这个变量名，供后续步骤用 ${alias} 引用。
    output_alias: str | None = None
    note: str | None = None


class FlowSpec(BaseModel):
    """一份待生成流程的整体描述。"""

    model_config = ConfigDict(extra="forbid")

    name: str = "自动生成的流程"
    id: str = "generated"
    inputs: dict = Field(default_factory=dict)
    steps: list[StepSpec]
    timeout_seconds: float = Field(default=3600, gt=0, le=86400)
