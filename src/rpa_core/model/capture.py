from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ElementDescriptor(BaseModel):
    """捕获产物：可回验命中的元素描述符（M10 元素库契约）。

    selector 语义按 kind 区分：
    - browser: ``{"css": "<css selector>", "candidates": [...] | 缺省}``
      ``css`` 必填且是第一顺位，语义与升级前**完全一致**；``candidates`` 可选，
      是捕获时额外收集的备选定位 ``[{"kind", "selector", "matchedCount"}]``。
    - desktop: ``{"locator": <DesktopLocator 文档>}``

    browser 的 ``metadata`` 另可携带语义特征（``role`` / ``accessibleName`` /
    ``placeholder`` / ``label`` / ``containerText``）与页面指纹（``url`` / ``title``），
    供将来元素改版后按候选排序。这些键必须放在 ``metadata`` 或 ``selector`` **内部**：
    描述符顶层与 metadata 一样是自由字典之处才行——顶层未知键会被 pydantic 默认的
    ``extra="ignore"`` **静默丢弃**（扩展曾长期回传顶层 ``url``，从未落盘即是此因）。
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


def _candidate_errors(element: ElementDescriptor) -> list[dict]:
    """``selector.candidates`` 可选；一旦出现就按 ``{kind, selector, matchedCount}`` 校验。

    候选是捕获时额外收集的备选定位（形状与影刀导出格式对齐：``kind`` + ``selector``），
    将来元素自愈要靠它排序。**缺省完全不校验**——既有元素文档只有 ``css``，
    必须继续合法；这是「加法兼容」的落点。`matchedCount` 是捕获时实测命中数，允许缺席。
    """
    errors: list[dict] = []
    raw = element.selector.get("candidates")
    if raw is None:
        return errors
    if not isinstance(raw, list):
        return [{"path": "selector.candidates", "message": "candidates 需要数组"}]
    for index, item in enumerate(raw):
        path = f"selector.candidates[{index}]"
        if not isinstance(item, dict):
            errors.append({"path": path, "message": "候选需要对象"})
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            errors.append({"path": f"{path}.kind", "message": "候选需要非空 kind"})
        selector = item.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            errors.append({"path": f"{path}.selector", "message": "候选需要非空 selector"})
        matched = item.get("matchedCount")
        if matched is not None and (
            isinstance(matched, bool) or not isinstance(matched, int) or matched < 1
        ):
            errors.append(
                {"path": f"{path}.matchedCount", "message": "matchedCount 需要 >=1 的整数"}
            )
    return errors


def selector_errors(element: ElementDescriptor) -> list[dict]:
    """selector 语义结构校验：browser 需非空 css；desktop 需合法 DesktopLocator。

    browser 另校验可选的 ``candidates``（见 ``_candidate_errors``）。
    """
    errors: list[dict] = []
    if element.kind == "browser":
        css = element.selector.get("css")
        if not isinstance(css, str) or not css.strip():
            errors.append({"path": "selector.css", "message": "browser 元素需要非空 css selector"})
        errors.extend(_candidate_errors(element))
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
