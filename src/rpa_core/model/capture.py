from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ElementDescriptor(BaseModel):
    """捕获产物：可回验命中的元素描述符（M10 元素库契约）。

    selector 语义按 kind 区分：
    - browser: {"css": "<css selector>"}
    - desktop: {"locator": <DesktopLocator 文档>}
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["browser", "desktop"]
    selector: dict[str, Any]
    verify_count: int = Field(default=1, alias="verifyCount", ge=0)
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), alias="capturedAt"
    )

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)
