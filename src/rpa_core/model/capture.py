from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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


class ElementDocumentError(ValueError):
    """元素文档不合法（结构/selector 语义），带 path/message 明细。"""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _validation_errors(exc: ValidationError) -> list[dict]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        errors.append({"path": location, "message": error["msg"]})
    return errors


def validate_element_document(body: dict) -> ElementDescriptor:
    """校验元素文档为合法 ElementDescriptor（CLI/devserver 共用能力层入口）。"""
    try:
        return ElementDescriptor.model_validate(body)
    except ValidationError as exc:
        detail = _validation_errors(exc)[0]
        raise ElementDocumentError(detail["path"], detail["message"]) from exc


def selector_errors(element: ElementDescriptor) -> list[dict]:
    """selector 语义结构校验：browser 需非空 css；desktop 需合法 DesktopLocator。"""
    errors: list[dict] = []
    if element.kind == "browser":
        css = element.selector.get("css")
        if not isinstance(css, str) or not css.strip():
            errors.append({"path": "selector.css", "message": "browser 元素需要非空 css selector"})
    elif element.kind == "desktop":
        locator = element.selector.get("locator")
        if not isinstance(locator, dict):
            errors.append({"path": "selector.locator", "message": "desktop 元素需要 locator 对象"})
            return errors
        from rpa_core.model.desktop import DesktopLocator

        try:
            DesktopLocator.model_validate(locator)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                errors.append({"path": f"selector.locator.{location}", "message": error["msg"]})
    return errors
