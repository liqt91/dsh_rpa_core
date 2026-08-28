from pydantic import BaseModel, ConfigDict, Field, model_validator


class DesktopLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_id: str | None = Field(default=None, alias="automationId", min_length=1)
    control_type: str | None = Field(default=None, alias="controlType", min_length=1)
    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_identity(self) -> "DesktopLocator":
        if not any((self.automation_id, self.control_type, self.name)):
            raise ValueError("desktop locator requires at least one identity field")
        return self
