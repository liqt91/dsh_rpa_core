import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

DesktopBackend = str

# LocatorStep 允许的键（D1/ADR 0018 定稿）：即 capture agent 现在能拿到的四个。
# className 参与等值匹配，不参与 matchMode（与 className 的既有口径一致）。
LOCATOR_STEP_KEYS: tuple[str, ...] = ("controlType", "automationId", "name", "className")

# matchMode 可用于的控件级字段（D2/ADR 0018 定稿）。**不含** className / controlId / foundIndex：
# 前者是等值口径（desktop.py 写死）、后两者是数值无匹配语义。
MATCH_MODE_FIELDS: tuple[str, ...] = ("automationId", "name")

VALID_MATCH_MODES: tuple[str, ...] = ("exact", "contains", "regex")


def prune_locator_steps(steps: list[dict]) -> list[dict]:
    """把「可能是草稿」的祖先链收成合法 ``path``：**每级保留四键中非空的、全空级丢弃**。

    **这是唯一权威**——捕获侧（`capture/desktop_agent._locator_step_for`：取不到任何键
    返回 None、调用方跳过）与 GUI 编辑器（M50：用户增删级 + 改级内键后重拼）**共用本函数**。
    M48 曾把「每级至少一键、全空级丢弃」这个口径隐式地写死在捕获侧；M50 要允许用户手编
    祖先链，若不抽出来共享，编辑器就成了**第二套权威**（同一件事两处实现必然漂移）。

    只认 ``LOCATOR_STEP_KEYS`` 四键；其余键丢弃（编辑器不该凭空发明级内字段）。
    值做 ``str`` 归一化后 strip，空串等价于「没给这个键」。
    """
    pruned: list[dict] = []
    for raw in steps:
        step: dict = {}
        source = raw if isinstance(raw, dict) else {}
        for key in LOCATOR_STEP_KEYS:
            value = source.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                step[key] = text
        if step:  # 一个键都没有 ⇒ 该级无法描述，丢弃（与捕获侧同口径）
            pruned.append(step)
    return pruned


def effective_match_mode(mode: str | None) -> str:
    """归一化 matchMode：None / 未知值一律当 ``exact``（兼容性硬要求）。

    未显式给 matchMode 时行为必须与今天逐字节一致——把 None 映射到 exact 是
    这条要求的唯一落点，故单独成函数并被两执行器共用（口径只有一处）。
    """
    return mode if mode in VALID_MATCH_MODES else "exact"


def matches_text(actual: str | None, expected: str, mode: str | None) -> bool:
    """按 matchMode 比较一个文本字段（D2 纯函数，两执行器共用）。

    - ``exact``（默认）：等值比较，**未给 matchMode 时即此路径**；
    - ``contains``：``expected in actual``（子串）；
    - ``regex``：``re.search(expected, actual)``——与 win32 `classNameRe` 同口径
      （search 而非整串锚定）。

    ``actual`` 为 None（控件没这个属性）恒不匹配。正则非法时**不抛**，按不匹配处理：
    定位器里的坏正则是个数据问题，让它表现为「找不到元素」而不是让执行器崩掉。
    """
    if actual is None:
        return False
    resolved = effective_match_mode(mode)
    if resolved == "exact":
        return actual == expected
    if resolved == "contains":
        return expected in actual
    try:
        return bool(re.search(expected, actual))
    except re.error:
        return False



class LocatorStep(BaseModel):
    """祖先链的一级（D1）。**不含目标本身**——目标仍由现有字段定位。

    path 是**收窄搜索域**的过滤条件，不是替代定位器：每级至少给一个键，
    执行器按层级逐级收窄后再在最后一级容器里找目标。
    """

    model_config = ConfigDict(extra="forbid")

    control_type: str | None = Field(default=None, alias="controlType", min_length=1)
    automation_id: str | None = Field(default=None, alias="automationId", min_length=1)
    name: str | None = Field(default=None, min_length=1)
    class_name: str | None = Field(default=None, alias="className", min_length=1)

    @model_validator(mode="after")
    def require_one_key(self) -> "LocatorStep":
        if not any((self.control_type, self.automation_id, self.name, self.class_name)):
            raise ValueError("locator path step requires at least one key")
        return self


class DesktopLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(default="uia", min_length=1)
    automation_id: str | None = Field(default=None, alias="automationId", min_length=1)
    control_type: str | None = Field(default=None, alias="controlType", min_length=1)
    name: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1)
    class_name: str | None = Field(default=None, alias="className", min_length=1)
    class_name_re: str | None = Field(default=None, alias="classNameRe", min_length=1)
    handle: int | None = Field(default=None, ge=1)
    control_id: int | None = Field(default=None, alias="controlId", ge=1)
    menu_path: list[str] | None = Field(default=None, alias="menuPath")
    found_index: int | None = Field(default=None, alias="foundIndex", ge=0)
    # D1：祖先链（从根窗口到目标的各级容器，不含目标本身）。可选——不出现时行为与今天一致。
    path: list[LocatorStep] | None = Field(default=None)
    # D2：控件级匹配方式（exact/contains/regex），作用于 automationId / name。
    # 默认 exact ⇒ 未显式给时行为零变化（兼容性的关键）。
    match_mode: str | None = Field(default=None, alias="matchMode")
    # D3：锚点起步（运行目标前先解析锚点；找不到报 ANCHOR_NOT_FOUND，不静默降级）。
    anchor: "AnchorSpec | None" = Field(default=None)

    @model_validator(mode="after")
    def require_identity(self) -> "DesktopLocator":
        if self.backend not in {"uia", "win32"}:
            raise ValueError("desktop locator backend must be uia or win32")
        if self.backend == "uia":
            if not any((self.automation_id, self.control_type, self.name)):
                raise ValueError("desktop locator requires at least one UIA identity field")
        else:
            if not any(
                (
                    self.title,
                    self.class_name,
                    self.class_name_re,
                    self.handle,
                    self.control_id,
                    self.menu_path,
                )
            ):
                raise ValueError("desktop locator requires at least one Win32 identity field")
            if self.class_name and self.class_name_re:
                raise ValueError(
                    "desktop locator className and classNameRe are mutually exclusive "
                    "(equality vs regular-expression matching)"
                )
        if self.menu_path is not None and not self.menu_path:
            raise ValueError("desktop locator menuPath cannot be empty")
        if self.path is not None and not self.path:
            raise ValueError("desktop locator path cannot be empty when provided")
        if self.match_mode is not None and self.match_mode not in VALID_MATCH_MODES:
            raise ValueError("desktop locator matchMode must be exact, contains or regex")
        if self.anchor is not None and self.anchor.locator.anchor is not None:
            # 锚点**不可嵌套**：anchor 里的 locator 再带 anchor 既无意义、又会把
            # 「解析顺序」变成无限递归的入口。D3 起步阶段直接拒绝（ADR 0018 §2 D3）。
            raise ValueError("desktop locator anchor cannot itself declare an anchor")
        return self


class AnchorSpec(BaseModel):
    """锚点起步（D3/ADR 0018）：目标元素定位**之前**先解析并确认存在的凭证元素。

    - `locator` 复用 `DesktopLocator` 本身——不发明第二套定位语言（ADR 0018 §2 D3.2）。
      因此 anchor 的 locator 同样享有 path / matchMode 能力，递归地一致。
    - `required` 语义由**运行期**承担（找不到即 `ANCHOR_NOT_FOUND`），本模型只承载形状。
    - **不可嵌套**：`locator.anchor` 必须为空（见 `DesktopLocator.require_identity`）。
    """

    model_config = ConfigDict(extra="forbid")

    locator: "DesktopLocator"


# `DesktopLocator.anchor` 与 `AnchorSpec.locator` 互为引用 ⇒ 在两者都定义后重建。
DesktopLocator.model_rebuild()

