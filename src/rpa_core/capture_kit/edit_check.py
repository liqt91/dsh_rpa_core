"""编辑期校验：回答「**这样改对不对**」（对齐影刀 §5「用户主动校验」）。

与隔壁 ``element_editing.validate_selector`` 的分工必须分清：

- ``validate_selector`` 回答「**能不能存**」——保存门槛，只查非空/单行/JSON 形状；
- 本模块回答「**编辑得对不对**」——结构是否自洽、当前这条选择器还能不能命中目标。

影刀把主动校验做成**一个入口 + 类型参数**：``Validate(selector, validateType)``，
类型决定走 ``ValidateSelector``（真定位，跨进程）/ ``ValidateSemantic``（AI 语义）/
``ValidateSelectorPath``（纯结构），IL 实证见《影刀元素校验机制报告》§5.1。
这里保持同一个形状：编辑框只认 ``check_draft``，不需要知道背后是纯函数还是跨进程。

- ``structure`` → 纯静态，微秒级，**绝不跨进程**；
- ``selector``  → 委托 ``capture.verification.verify_element``（flash 语义：置前 + 红框）；
- ``semantic``  → 我们没有 AI 语义定位，如实返回 ``unsupported``，不假装成功。

★ **只读**：本模块不修改描述符、不落盘。影刀同一口径（§5.5「主动校验只高亮、
不修改选择器」）。

★ ``selector`` 分支会**原样带上原描述符的 ``metadata``**：桌面腿靠它取
``metadata.windowHandle`` 把查找限定在捕获时那个窗口（``_expected_hwnd``）——
既快，又不会误命中别的窗口里同名控件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

# 与影刀 ``validateType`` 同构；多一个值就多一条路径，别偷偷加。
VALIDATE_TYPES = ("structure", "selector", "semantic")

# 属性选择器缺值：``[title=]`` / ``[title=""]`` 之外的空值形态
_EMPTY_ATTR = re.compile(r"\[\s*[\w-]+\s*=\s*\]")
# 组合符结尾 = 后面还缺一层（``div.a >``）；``>>`` 之类不用管，括号配对会先报
_TRAILING_COMBINATOR = (">", "+", "~", ",")


@dataclass(frozen=True)
class EditCheckResult:
    """编辑期校验结果（字段对齐影刀 ``ValidateResult``，§5.2）。

    ``success`` 的口径是**恰好命中 1 个**——0 个和多个都不算通过。但这只是
    提示口径：编辑框**不拿它拦保存**（弹窗类元素编辑时本来就不在屏幕上）。
    """

    success: bool
    count: int = 0
    # 多匹配时指出第几个（影刀 ``ErrorIndex``）；我们目前只报总数，恒 -1。
    error_index: int = -1
    # 结构化原因码（对应影刀 ``UiaErrorCode``）：syntax / unsupported / bad-reply /
    # find-failed / tree-missing / desktop-verifier-unavailable …
    error_code: str | None = None
    message: str = ""
    # 用户在等待期间中止（影刀 ``IsCancel``）；本层只透传，不负责取消。
    cancelled: bool = False
    rect: dict[str, Any] | None = None
    window_handle: int | None = None
    shot: dict[str, Any] | None = None


def _browser_structure(css: str) -> str | None:
    """网页选择器的结构自洽性（不联网、不问浏览器）。"""
    text = (css or "").strip()
    if not text:
        return "选择器不能为空"
    if "\n" in text or "\r" in text:
        return "网页选择器只能是一行"

    depth_paren = 0
    depth_bracket = 0
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth_paren += 1
        elif char == ")":
            depth_paren -= 1
            if depth_paren < 0:
                return "选择器括号不配对"
        elif char == "[":
            depth_bracket += 1
        elif char == "]":
            depth_bracket -= 1
            if depth_bracket < 0:
                return "选择器方括号不配对"

    if quote:
        return "选择器的引号没有闭合"
    if depth_paren or depth_bracket:
        return "选择器括号不配对"
    if _EMPTY_ATTR.search(text):
        return "属性选择器缺少值"
    if text.endswith(_TRAILING_COMBINATOR):
        return f"选择器以 {text[-1]} 结尾，后面还缺一层"
    return None


# pydantic 的英文报错 → 编辑框里能看懂的一句话。只映射已知的几条，
# 其余用通用前缀兜底——**不猜语义**，宁可原样带上原文。
_LOCATOR_HINTS = (
    (
        "requires at least one UIA identity field",
        "桌面定位至少要保留一个身份字段（控件类型 / 名称 / AutomationId）",
    ),
    (
        "requires at least one win32 identity field",
        "桌面定位至少要保留一个身份字段（类名 / 标题 / 控件 ID）",
    ),
    ("must be uia or win32", "定位后端只能是 uia 或 win32"),
)


def _locator_error_message(exc: Exception) -> str:
    text = str(exc)
    for needle, hint in _LOCATOR_HINTS:
        if needle in text:
            return hint
    return "桌面定位不合法：至少要保留一个身份字段"


def _desktop_structure(selector: Any) -> str | None:
    """桌面 locator 的结构自洽性——**直接用模型校验**，不另写一份规则。"""
    if not isinstance(selector, dict):
        return "桌面定位必须是 JSON 对象"
    locator = selector.get("locator")
    if not isinstance(locator, dict):
        return '桌面定位缺少 "locator" 字段'
    if not locator:
        return "桌面定位是空的，至少保留目标节点的一个特征"
    try:
        from rpa_core.model.desktop import DesktopLocator
    except Exception:  # noqa: BLE001 - 模型取不到就别假装校验过
        return None
    try:
        DesktopLocator.model_validate(locator)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError 族
        return _locator_error_message(exc)
    return None


def check_structure(kind: Any, selector: Any) -> str | None:
    """结构自洽性：错误文案或 ``None``（**纯函数，不碰任何外部状态**）。"""
    if kind == "browser":
        css = selector.get("css") if isinstance(selector, dict) else selector
        return _browser_structure(str(css or ""))
    if kind == "desktop":
        return _desktop_structure(selector)
    return f"不认识的元素类型：{kind or '(空)'}"


def _message_for(result: EditCheckResult) -> str:
    """把结果翻成编辑框里的那一行字（对齐影刀 UI 文案的口气）。"""
    if result.cancelled:
        return "校验已取消"
    if result.error_code == "syntax":
        return result.message or "选择器结构不对"
    if result.error_code == "unsupported":
        return result.message or "暂不支持这种校验"
    if result.error_code == "tree-missing":
        return "未找到元素：目标窗口的无障碍树还没建起来，先点一下该窗口再校验"
    if result.error_code:
        return f"无法校验：{result.error_code}"
    if result.count == 1:
        return "找到 1 个元素"
    if result.count > 1:
        return f"找到 {result.count} 个元素，不唯一，可以再勾一层属性收窄"
    return "未找到元素，请确认窗口已激活"


def check_draft(
    descriptor: dict[str, Any],
    selector: Any,
    *,
    kind: str | None = None,
    validate_type: str = "selector",
    verifier: Any = None,
    want_shot: bool = True,
) -> EditCheckResult:
    """对「当前编辑态」跑一次校验（不落盘、不改描述符）。

    ``descriptor`` 只用来取 ``kind`` 与 ``metadata``（窗口限定）；``selector`` 是
    编辑框里**当前勾选/输入**的结果：browser 传 css 文本，desktop 传完整
    selector 字典（``compose_selector`` 的产物）。

    ``kind`` 显式给出时优先——网页层级形态的编辑框拿不到完整描述符，只有层级模型，
    但它确知自己是 browser。

    ``verifier`` 是桌面 verifier 的注入口，测试用它证明「委托到了活体那一层」，
    不必真开窗口。
    """
    if validate_type not in VALIDATE_TYPES:
        return EditCheckResult(
            success=False,
            error_code="unsupported-type",
            message=f"不认识的校验类型：{validate_type}",
        )

    resolved_kind = kind or descriptor.get("kind")

    if validate_type == "semantic":
        return EditCheckResult(
            success=False,
            error_code="unsupported",
            message="暂不支持 AI 语义定位校验",
        )

    # ★ 结构不对就别跨进程了：先本地拦下，省一次往返，也避免把语法错误
    # 报成「未找到元素」（用户会以为元素没了）。
    structure_error = check_structure(resolved_kind, selector)
    if structure_error:
        return EditCheckResult(
            success=False, error_code="syntax", message=structure_error
        )
    if validate_type == "structure":
        return EditCheckResult(success=True, message="结构没问题")

    if resolved_kind == "browser":
        selector_dict: Any = {"css": str(selector or "").strip()}
    else:
        selector_dict = selector

    draft: dict[str, Any] = {"kind": resolved_kind, "selector": selector_dict}
    metadata = descriptor.get("metadata")
    if isinstance(metadata, dict):
        draft["metadata"] = metadata

    from rpa_core.capture.verification import verify_element

    try:
        result = verify_element(
            draft, mode="flash", want_shot=want_shot, desktop_verifier=verifier
        )
    except Exception as exc:  # noqa: BLE001 - 校验通道炸了也不能把编辑框打死
        return EditCheckResult(
            success=False,
            error_code=f"verify-exception:{type(exc).__name__}",
            message="校验通道出错，稍后再试",
        )

    payload = result.to_dict()
    error = payload.get("error")
    # 「找到了但树没建起来」要单独说，否则用户会去改一个其实没错的选择器。
    error_code = (
        "tree-missing" if payload.get("hint") == "tree-missing"
        else (str(error) if error else None)
    )
    rect = payload.get("rect")
    shot = payload.get("shot")
    checked = EditCheckResult(
        success=False,
        count=int(payload.get("count") or 0),
        error_code=error_code,
        rect=rect if isinstance(rect, dict) else None,
        window_handle=payload.get("windowHandle"),
        shot=shot if isinstance(shot, dict) else None,
    )
    # 恰好 1 个才算过（影刀 ``IsSuccess`` 口径，§5.2）。
    return replace(
        checked,
        success=checked.count == 1 and not checked.error_code,
        message=_message_for(checked),
    )


__all__ = [
    "VALIDATE_TYPES",
    "EditCheckResult",
    "check_draft",
    "check_structure",
]
