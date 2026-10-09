"""自然语言 → 操作步骤 的解释器。

提供：
- `PlanInterpreter`：协议（Protocol）。想接 LLM 后端时，实现 `interpret(text, *, catalog)`
  返回 `InterpretedStep | None` 即可，其余生成链路无需改动。
- `KeywordInterpreter`：默认实现，纯规则 + 正则，零外部依赖、可单测、可复现。

严格原则：某步无法被**完整**参数化（缺选择器、网址推断不出等）就抛 `FlowGenerationError`，
绝不吐半成品。这是「避免产出无法运行的流程」在解释阶段的第一道闸门。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from rpa_core.catalog import CommandCatalog

from .errors import FlowGenerationError

# 常见站点名 → 官网（NL 里写「打开百度」也能落地成 URL）。
_SITE_MAP: dict[str, str] = {
    "百度": "https://www.baidu.com",
    "baidu": "https://www.baidu.com",
    "谷歌": "https://www.google.com",
    "google": "https://www.google.com",
    "必应": "https://www.bing.com",
    "bing": "https://www.bing.com",
}

_URL_RE = re.compile(r"https?://[^\s，。；,;]+")
_HOST_RE = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
# 选择器形态：#id / .class / //xpath / 带引号的选择器
_SELECTOR_RE = re.compile(r"(#[\w-]+|\.[\w-]+|//\S+|['\"][^'\"]+['\"])")


@dataclass
class InterpretedStep:
    """解释器对一段自然语言得出的结构化结果。"""

    command: str
    with_: dict = field(default_factory=dict)
    # 输出别名建议（如把 navigate 的 sessionId 挂到变量）。
    output_alias: str | None = None
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class PlanInterpreter(Protocol):
    """NL → 步骤 的解释器协议。"""

    def interpret(self, text: str, *, catalog: CommandCatalog) -> InterpretedStep | None:
        """解释一段自然语言。

        返回 None 表示无法识别（交由上层报错或跳过）；抛出异常表示识别出但无法完整参数化。
        """
        ...


class KeywordInterpreter:
    """基于关键词/正则的确定性解释器（默认实现）。"""

    def interpret(self, text: str, *, catalog: CommandCatalog) -> InterpretedStep | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        # 顺序即优先级：更具体的意图（导航/截图）放在更靠前。
        for handler in (
            self._navigate,
            self._screenshot,
            self._scroll,
            self._wait_visible,
            self._sleep,
            self._get_text,
            self._click,
            self._input,
            self._set_var,
            self._datetime_now,
            self._log,
        ):
            result = handler(cleaned, catalog)
            if result is not None:
                return result
        return None

    # ---- 各意图处理 -------------------------------------------------------

    def _navigate(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"打开|访问|导航|进入|goto|navigate|open", text, re.I):
            return None
        m = _URL_RE.search(text)
        if m:
            url = m.group(0).rstrip("/.,;")
        else:
            m2 = re.search(
                r"(?:打开|访问|导航|进入|goto|navigate|open)\s*[:：]?\s*([^\s，。；,;]+)",
                text,
                re.I,
            )
            token = m2.group(1) if m2 else None
            if not token:
                raise FlowGenerationError(
                    f"无法从「{text}」推断目标网址：请给出完整 URL，例如「打开 https://example.com」"
                )
            low = token.lower()
            if low in _SITE_MAP:
                url = _SITE_MAP[low]
            elif _HOST_RE.match(token):
                url = token if token.startswith("http") else f"https://{token}"
            else:
                raise FlowGenerationError(
                    f"无法识别站点「{token}」，请给出完整 URL（如 https://example.com）"
                )
        # 默认 chrome；导航步的主输出 sessionId 由 builder 自动挂到 page_<n> 变量。
        return InterpretedStep(
            command="browser.navigate",
            with_={"browserType": "chrome", "action": "goto", "url": url},
            output_alias=None,
        )

    def _screenshot(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"截图|截屏|screenshot", text, re.I):
            return None
        return InterpretedStep(
            command="browser.screenshot",
            # savePath 由 builder 按步序号补全（outputs/screenshot_<n>.png）
            with_={"savePath": "__AUTO_SCREENSHOT__"},
        )

    def _scroll(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        m = re.search(r"滚动|下拉|上滑|scroll", text, re.I)
        if not m:
            return None
        position = "bottom"
        if re.search(r"顶部|最上|top", text, re.I):
            position = "top"
        return InterpretedStep(
            command="browser.scroll",
            with_={"position": position},
        )

    def _wait_visible(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        m = re.search(r"等待|wait", text, re.I)
        if not m:
            return None
        # 等待 N 秒/毫秒 → 走 sleep，不在此处理（见 _sleep）。
        if re.search(r"\d+\s*(秒|s|毫秒|ms|millisecond)", text, re.I):
            return None
        sel = self._extract_selector(text)
        if sel is None:
            raise FlowGenerationError(
                f"无法为「{text}」推断要等待的元素：请给出选择器，例如「等待 #loading 出现」"
            )
        state = "visible"
        if re.search(r"消失|隐藏|不见", text):
            state = "hidden"
        return InterpretedStep(
            command="browser.waitFor",
            with_={"selector": sel, "state": state},
        )

    def _sleep(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        m = re.search(r"等待|延时|暂停|休眠|sleep|delay|wait", text, re.I)
        if not m:
            return None
        num = re.search(r"(\d+(?:\.\d+)?)\s*(秒|s|毫秒|ms|millisecond)", text, re.I)
        if not num:
            return None
        value = float(num.group(1))
        seconds = value if num.group(2) in ("秒", "s", "second", "seconds") else value / 1000.0
        return InterpretedStep(command="workflow.sleep", with_={"seconds": seconds})

    def _get_text(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"获取文本|抓取|读取文本|提取文本|getText|get text", text, re.I):
            return None
        sel = self._extract_selector(text)
        if sel is None:
            raise FlowGenerationError(
                f"无法为「{text}」推断要取文本的元素：请给出选择器，例如「获取 #title 的文本」"
            )
        return InterpretedStep(command="browser.getText", with_={"selector": sel})

    def _click(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"点击|单击|点一下|click|tap", text, re.I):
            return None
        sel = self._extract_selector(text)
        if sel is None:
            raise FlowGenerationError(
                f"无法为「{text}」推断要点击的元素：请给出选择器，例如「点击 #submit」"
            )
        return InterpretedStep(command="browser.click", with_={"selector": sel})

    def _input(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"输入|填写|键入|打字|input|type", text, re.I):
            return None
        # 形态 A：「在 <选择器> 输入 <值>」
        ma = re.search(
            r"在\s*"
            + _SELECTOR_RE.pattern
            + r"\s*(?:里|中|框|输入框|元素)?\s*(?:输入|填写|键入|打字)\s*[:：]?\s*(.+)",
            text,
            re.I,
        )
        # 形态 B：「输入 <值> 到/在 <选择器>」
        mb = re.search(
            r"(?:输入|填写|键入|打字)\s*[:：]?\s*(.+?)\s*(?:到|在)\s*"
            + _SELECTOR_RE.pattern,
            text,
            re.I,
        )
        selector = value = None
        if ma:
            selector, value = ma.group(1), ma.group(2).strip()
        elif mb:
            value, selector = mb.group(1).strip(), mb.group(2)
        else:
            # 仅「输入 <值>」：无选择器无法落地 → 报错（严格原则）
            mv = re.search(r"(?:输入|填写|键入|打字)\s*[:：]?\s*(.+)", text, re.I)
            value = mv.group(1).strip() if mv else ""
            raise FlowGenerationError(
                f"无法为「{text}」推断输入框选择器：请给出选择器，"
                f"例如「在 #search 输入 {value}」或「输入 {value} 到 #search」"
            )
        with_ = {"selector": selector, "text": value}
        press = re.search(r"回车|enter", text, re.I)
        if press:
            with_["pressEnter"] = True
        return InterpretedStep(command="browser.input", with_=with_)

    def _set_var(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        m = re.search(
            r"(?:设置?变量|定义变量|把)\s*([A-Za-z_]\w*)\s*(?:设为|=|:|＝)\s*(.+?)(?:$|。|；|;)",
            text,
            re.I,
        )
        if not m:
            return None
        name, raw = m.group(1), m.group(2).strip().strip("'\"")
        var_type, value = self._infer_value(raw)
        return InterpretedStep(
            command="data.setVar",
            with_={"varName": name, "varType": var_type, "value": value},
        )

    def _datetime_now(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        if not re.search(r"当前时间|时间戳|现在几点|现在时间|datetime|now", text, re.I):
            return None
        return InterpretedStep(command="data.datetimeNow", with_={})

    def _log(self, text: str, _catalog: CommandCatalog) -> InterpretedStep | None:
        m = re.search(r"打印|输出日志|记录日志|日志|log|打印日志", text, re.I)
        if not m:
            return None
        msg = re.sub(r"^(?:打印|输出日志|记录日志|日志|log)\s*[:：]?", "", text, flags=re.I).strip()
        return InterpretedStep(command="data.log", with_={"message": msg or text})

    # ---- 辅助 -------------------------------------------------------------

    @staticmethod
    def _extract_selector(text: str) -> str | None:
        m = _SELECTOR_RE.search(text)
        if not m:
            return None
        sel = m.group(1)
        return sel.strip("'\"")

    @staticmethod
    def _infer_value(raw: str) -> tuple[str, object]:
        low = raw.lower()
        if low in ("true", "false"):
            return "boolean", low == "true"
        if re.fullmatch(r"-?\d+", raw):
            return "number", int(raw)
        if re.fullmatch(r"-?\d+\.\d+", raw):
            return "number", float(raw)
        return "string", raw
