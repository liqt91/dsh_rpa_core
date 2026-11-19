from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    # M52 S6 元素引用去快照：节点引用的元素在**元素库**里找不到（被删/改名/文件坏）时
    # 必须显式失败。旧实现靠 `with` 里的值快照回落——那正是「一份参数两份真值」的来源，
    # 也是「选了元素、selector 却没落进节点」这类事故的土壤。快照已去掉 ⇒ 没有兜底，
    # 语义等同 ANCHOR_NOT_FOUND：**宁可停，不静默用旧值跑**。
    ELEMENT_REF_MISSING = "ELEMENT_REF_MISSING"
    # M48 D3 锚点：`locator.anchor` 声明的锚点元素在运行期找不到时必须**显式失败**，
    # 绝不静默回落到「直接找目标」——静默降级正是本项目最忌讳的一类错误（ADR 0018 §2 D3）。
    ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
    # M28 S2 执行前预检的稳定分类：命中元素但不可安全操作时必须**显式失败**，
    # 绝不静默点到遮罩层/禁用控件上（否则「点错地方」会伪装成成功）。
    ELEMENT_COVERED = "ELEMENT_COVERED"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
    # M107 S2：目标协议 op（如 `cdp.click`）**只有新版本扩展才有**。同一个人机器上
    # 两个浏览器各自装着一份插件、版本可以差好几个小版本（实测 Chrome 0.9.2 /
    # Edge 0.9.7），而流程只写了 `browserType`。此时旧扩展只会回一句
    # `unsupported op`，看起来像通道坏了 ⇒ 单列一个码，直接说「哪个浏览器、装的
    # 是哪版、要哪版」，用户照着点一下「重新加载」就好了。
    EXTENSION_OUTDATED = "EXTENSION_OUTDATED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_LOST = "SESSION_LOST"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXECUTOR_FAILED = "EXECUTOR_FAILED"
    SCRIPT_FAILED = "SCRIPT_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    PLATFORM_UNSUPPORTED = "PLATFORM_UNSUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RpaError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
