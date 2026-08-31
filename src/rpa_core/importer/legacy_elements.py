from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rpa_core.model.desktop import DesktopLocator


class ImportDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str = Field(pattern=r"^(info|warning|error)$")
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    source_path: str = Field(min_length=1)


class ImportedElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(alias="elementId", min_length=1)
    source_path: str = Field(alias="sourcePath", min_length=1)
    source_index: int = Field(alias="sourceIndex", ge=0)
    backend: str = Field(min_length=1)
    locator: DesktopLocator
    legacy_identity: dict[str, Any] = Field(alias="legacyIdentity", default_factory=dict)


class ImportedElementCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(alias="sourceFile", min_length=1)
    version: int = Field(ge=1)
    elements: list[ImportedElement] = Field(default_factory=list)
    diagnostics: list[ImportDiagnostic] = Field(default_factory=list)


@dataclass(frozen=True)
class LegacyElementImporter:
    source_file: Path

    def import_data(self, data: dict[str, Any]) -> ImportedElementCatalog:
        diagnostics: list[ImportDiagnostic] = []
        elements: list[ImportedElement] = []

        version = int(data.get("version", 1))
        for index, raw in enumerate(data.get("elements", [])):
            imported, item_diagnostics = self._import_element(raw, index)
            if imported is not None:
                elements.append(imported)
            diagnostics.extend(item_diagnostics)

        extra_keys = sorted(
            key
            for key in data.keys()
            if key not in {"version", "elements"}
        )
        if extra_keys:
            diagnostics.append(
                ImportDiagnostic(
                    severity="info",
                    code="CATALOG_EXTRA_KEYS",
                    message=f"catalog ignored top-level keys: {', '.join(extra_keys)}",
                    source_path=str(self.source_file),
                )
            )

        return ImportedElementCatalog(
            sourceFile=str(self.source_file),
            version=version,
            elements=elements,
            diagnostics=diagnostics,
        )

    def _import_element(
        self, raw: dict[str, Any], index: int
    ) -> tuple[ImportedElement | None, list[ImportDiagnostic]]:
        diagnostics: list[ImportDiagnostic] = []
        element_id = str(raw.get("id") or raw.get("name") or f"element-{index}")
        element_type = str(raw.get("element_type") or raw.get("elementType") or "web")
        source_path = self.source_file.as_posix()
        legacy_identity = {
            key: raw.get(key)
            for key in (
                "id",
                "name",
                "title",
                "element_type",
                "control_type",
                "automation_id",
                "class_name",
            )
            if raw.get(key) is not None
        }

        if element_type in {"uia", "win32"}:
            locator = self._import_desktop_locator(raw, element_type, source_path, diagnostics)
        else:
            locator = self._import_web_locator(raw, source_path, diagnostics)

        if locator is None:
            return None, diagnostics

        return (
            ImportedElement(
                elementId=element_id,
                sourcePath=source_path,
                sourceIndex=index,
                backend=locator.backend,
                locator=locator,
                legacyIdentity=legacy_identity,
            ),
            diagnostics,
        )

    def _import_web_locator(
        self, raw: dict[str, Any], source_path: str, diagnostics: list[ImportDiagnostic]
    ) -> DesktopLocator | None:
        candidates = raw.get("candidates") or []
        selector = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("kind") in {"css", "xpath"} and candidate.get("selector"):
                selector = str(candidate["selector"])
                break
        if selector is None:
            selector = str(raw.get("css_selector") or raw.get("xpath") or raw.get("dom_path") or "")
        if not selector:
            diagnostics.append(
                ImportDiagnostic(
                    severity="warning",
                    code="WEB_NO_SELECTOR",
                    message="web element has no supported selector candidate",
                    source_path=source_path,
                )
            )
            return None
        unsupported = sorted(
            key
            for key in raw.keys()
            if key
            not in {
                "id",
                "name",
                "element_type",
                "elementType",
                "candidates",
                "css_selector",
                "xpath",
                "dom_path",
            }
        )
        if unsupported:
            diagnostics.append(
                ImportDiagnostic(
                    severity="info",
                    code="WEB_IGNORED_FIELDS",
                    message=f"web element ignored fields: {', '.join(unsupported)}",
                    source_path=source_path,
                )
            )
        return DesktopLocator.model_validate({"backend": "uia", "name": selector})

    def _import_desktop_locator(
        self,
        raw: dict[str, Any],
        element_type: str,
        source_path: str,
        diagnostics: list[ImportDiagnostic],
    ) -> DesktopLocator | None:
        path_key = "uia_path" if element_type == "uia" else "win32_path"
        path = raw.get(path_key) or []
        if not isinstance(path, list) or not path:
            diagnostics.append(
                ImportDiagnostic(
                    severity="warning",
                    code="DESKTOP_PATH_MISSING",
                    message=f"{path_key} is missing or empty",
                    source_path=source_path,
                )
            )
            return None
        leaf = path[-1] if isinstance(path[-1], dict) else {}
        if element_type == "uia":
            unsupported = sorted(
                key
                for key in raw.keys()
                if key
                not in {
                    "id",
                    "name",
                    "element_type",
                    "elementType",
                    "uia_path",
                    "control_type",
                    "automation_id",
                    "class_name",
                    "title",
                }
            )
            if unsupported:
                diagnostics.append(
                    ImportDiagnostic(
                        severity="info",
                        code="UIA_IGNORED_FIELDS",
                        message=f"uia element ignored fields: {', '.join(unsupported)}",
                        source_path=source_path,
                    )
                )
            return DesktopLocator.model_validate(
                {
                    "backend": "uia",
                    "automationId": self._non_empty(
                        leaf.get("automation_id") or leaf.get("automationId")
                    ),
                    "controlType": self._non_empty(
                        leaf.get("control_type") or leaf.get("controlType")
                    ),
                    "name": self._non_empty(leaf.get("name")),
                }
            )
        return DesktopLocator.model_validate(
            {
                "backend": "win32",
                "title": self._non_empty(leaf.get("title") or leaf.get("name")),
                "className": self._non_empty(leaf.get("class_name") or leaf.get("className")),
                "controlId": leaf.get("control_id") or leaf.get("controlId"),
                "foundIndex": leaf.get("index") or leaf.get("foundIndex"),
            }
        )

    @staticmethod
    def _non_empty(value: Any) -> Any:
        if value == "":
            return None
        return value
