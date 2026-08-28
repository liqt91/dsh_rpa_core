from pydantic import BaseModel, ConfigDict

from rpa_core.model.workflow import Workflow


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow: Workflow
    catalog_digest: str
    required_capabilities: frozenset[str]
