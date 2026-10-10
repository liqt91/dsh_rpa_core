import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rpa_core.model.selector_ranking import looks_generated_id, rank_candidates

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



# M52：桌面 locator 优选（唯一性硬门 + penalty 最小化）。
# penalty 表是**稳定先验**，依据 2026-10-10 开源调研（@medv/finder 的 penalty 表、
# Robula+ 的属性白/黑名单、Playwright codegen 的语义阶梯）：值越小越稳。
# **表可调**——判据只钉「表被读到、合成生效」，不钉具体数值（否则调表即静默变红）。
LOCATOR_FIELD_PENALTIES: dict[str, int] = {
    "automationId": 0,   # 最稳：固定 id（像框架生成哈希时改按 HASHY_ID_PENALTY 降权）
    "className": 15,     # WinForms 类名（机器级常量）
    "classNameRe": 15,   # 同 className，正则形态（用于含动态哈希的类名）
    "name": 20,          # 可见文本 / 可访问名（语义强，但文本可能随内容变）
    "title": 20,         # Win32 窗口标题
    "menuPath": 30,      # 菜单路径
    "controlType": 40,   # 弱：几乎不唯一
    "foundIndex": 80,    # 位置序号：只活当次快照
    "controlId": 90,     # 每次进程启动都变
    "handle": 100,       # 运行期值（每次启动都变）
}

PATH_STEP_PENALTY = 12   # 祖先链每级（越长越脆）
HASHY_ID_PENALTY = 70    # automationId 形如框架生成哈希时改用此值


# ``looks_generated_id``（"像哈希的 id"判定）已迁至 ``model.selector_ranking``——
# 那里是**两端共用**的稳定性先验（桌面 automationId 与浏览器 ``#id`` 都靠它降权），
# 由顶部 import 引入本模块（re-export），既有
# ``from rpa_core.model.desktop import looks_generated_id`` 的调用点（含 S1 判据）继续可用。


def locator_penalty(locator: dict) -> int:
    """一个桌面 locator（dict 形态）的 penalty 总和——**越小越稳**。

    纯函数、表驱动：命中任一 ``LOCATOR_FIELD_PENALTIES`` 字段即累加该字段 penalty；
    ``automationId`` 单独按 ``looks_generated_id`` 在 0 / ``HASHY_ID_PENALTY`` 之间取值；
    祖先链 ``path`` 按级数乘 ``PATH_STEP_PENALTY``。字段**存在**即计入（不看值）。
    """
    total = 0
    automation_id = locator.get("automationId")
    for field, penalty in LOCATOR_FIELD_PENALTIES.items():
        if field == "automationId":
            continue
        if locator.get(field) is not None:
            total += penalty
    if automation_id is not None:
        total += (
            HASHY_ID_PENALTY
            if looks_generated_id(str(automation_id))
            else LOCATOR_FIELD_PENALTIES["automationId"]
        )
    path = locator.get("path")
    if isinstance(path, list):
        total += PATH_STEP_PENALTY * len(path)
    return total


def choose_best_locator(entries: list[dict]) -> dict | None:
    """从候选中择优：**唯一性硬门 + penalty 最小化 + 稳定序**（M52 核心，单一权威）。

    每条 entry 形如 ``{"locator": <dict>, "count": <实测命中数 int>}``。

    规则（对齐 @medv/finder 的「唯一即停 + penalty 排序」）：
    **字典序比较 ``(count, penalty)``**——``count`` 是**第一关键字**，故
    「唯一命中（count==1）恒优先于任何 penalty」即是「**唯一性硬门**」；若反过来先比
    penalty，一条 count=2 但更稳的选择器会压过唯一的那条（那是**加权口径**，不是硬门）。
    同 count 再比 penalty；**完全并列时保留输入序** ⇒ 稳定序（防 flaky）。
    空列表 / 无有效 count → 返回 ``None``（调用方自行回退）。

    排序引擎（``rank_candidates``）与浏览器腿**共用同一份**（``model.selector_ranking``）；
    本函数只提供桌面侧的 penalty 分派（``locator_penalty``）与合法性清洗。

    与旧实现（``capture_at`` 里「遇 count==1 即 break」）的**有意差异**：旧实现取**首个**
    唯一命中，本函数在所有候选里取「count 最小、其次 penalty 最小」的那条——可能选到
    penalty 更低者，这是 M52 的改进目标。
    """
    usable = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("locator"), dict)
        and isinstance(entry.get("count"), int)
        and not isinstance(entry.get("count"), bool)
    ]
    if not usable:
        return None
    ranked = rank_candidates(
        usable,
        count_of=lambda entry: entry["count"],
        penalty_of=lambda entry: locator_penalty(entry["locator"]),
    )
    return ranked[0]


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

