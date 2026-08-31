from pydantic import BaseModel, ConfigDict, Field, model_validator

DesktopBackend = str


class DesktopLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(default="uia", min_length=1)
    automation_id: str | None = Field(default=None, alias="automationId", min_length=1)
    control_type: str | None = Field(default=None, alias="controlType", min_length=1)
    name: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1)
    class_name: str | None = Field(default=None, alias="className", min_length=1)
    handle: int | None = Field(default=None, ge=1)
    control_id: int | None = Field(default=None, alias="controlId", ge=1)
    menu_path: list[str] | None = Field(default=None, alias="menuPath")
    found_index: int | None = Field(default=None, alias="foundIndex", ge=0)

    @model_validator(mode="after")
    def require_identity(self) -> "DesktopLocator":
        if self.backend not in {"uia", "win32"}:
            raise ValueError("desktop locator backend must be uia or win32")
        if self.backend == "uia":
            if not any((self.automation_id, self.control_type, self.name)):
                raise ValueError("desktop locator requires at least one UIA identity field")
        else:
            if not any((self.title, self.class_name, self.handle, self.control_id, self.menu_path)):
                raise ValueError("desktop locator requires at least one Win32 identity field")
        if self.menu_path is not None and not self.menu_path:
            raise ValueError("desktop locator menuPath cannot be empty")
        return self
