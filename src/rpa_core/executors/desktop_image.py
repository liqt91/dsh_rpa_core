"""桌面图像定位执行器（M75 S4，ADR 0003 分层第 4 层）。

只承载一个命令 ``desktop.imageLocate``：**显式**用图像/OCR 在指定窗口里定位控件，
回屏幕绝对矩形。它自己不做静默降级——语义（UIA/win32）找不到时是否改走本命令，
由 workflow 显式选择（见 ``capture.image_guard``）。本执行器就是 ADR 0003 要求的
「明确选择的不同 driver」，回传的 ``diagnostics.usedLayer="image"`` 是实际使用层证据。

实际定位复用 ``capture.image_verification.ImageElementVerifier``（M75 S3）——
不建第二套图像定位逻辑。参数/错误码都写进 ``if command == ...`` 分支体，
供 check_param_consumption / check_error_contract 字面量切片。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any

from rpa_core.capture.image_guard import TEMPLATE_SCHEMA
from rpa_core.model.command import CommandInvocation, CommandResult
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor


class DesktopImageExecutor(CommandExecutor):
    """图像定位执行器（进程内同步定位 + 线程池执行，不启子进程）。"""

    def __init__(self, verifier: Any = None) -> None:
        self._verifier = verifier

    def _verifier_of(self) -> Any:
        if self._verifier is not None:
            return self._verifier
        from rpa_core.capture.image_verification import ImageElementVerifier

        return ImageElementVerifier()

    def _execute_sync(self, invocation: CommandInvocation) -> CommandResult:
        command = invocation.command_id
        inputs = invocation.inputs
        if command == "desktop.imageLocate":
            # 所有 manifest 声明的输入都在这里字面量读取（参数门禁口径）
            window_handle = inputs.get("windowHandle")
            template_b64 = inputs.get("imageTemplate")
            keyword = inputs.get("imageKeyword")
            template_size = inputs.get("templateSize")
            min_confidence = inputs.get("minConfidence")

            if (
                not isinstance(window_handle, int)
                or isinstance(window_handle, bool)
                or window_handle <= 0
            ):
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT, "windowHandle must be a positive integer"
                )
            if not template_b64 and not keyword:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT,
                    "exactly one of imageTemplate / imageKeyword is required",
                )
            if template_b64 and keyword:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT, "imageTemplate and imageKeyword are mutually exclusive"
                )

            locator: dict[str, Any] = {
                "driver": "image",  # 显式选择：守卫据此放行图像层
                "windowHandle": window_handle,
            }
            if template_b64:
                locator["imageTemplate"] = template_b64
                locator["templateSchema"] = TEMPLATE_SCHEMA
                try:
                    raw_template = base64.b64decode(str(template_b64), validate=True)
                except Exception as exc:  # noqa: BLE001 - 输入不是合法 base64
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, f"imageTemplate is not valid base64: {exc}"
                    )
                locator["templateHash"] = hashlib.sha256(raw_template).hexdigest()
                if template_size:
                    locator["templateSize"] = template_size
                if min_confidence is not None:
                    locator["minConfidence"] = min_confidence
            if keyword:
                locator["imageKeyword"] = keyword

            verifier = self._verifier_of()
            reply = verifier.verify(locator)
            if isinstance(reply, dict) and reply.get("error"):
                return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(reply["error"]))
            if not isinstance(reply, dict) or int(reply.get("count", 0)) < 1:
                return CommandResult.failure(ErrorCode.ELEMENT_NOT_FOUND, "image target not found")

            rect = dict(reply.get("rect") or {})
            return CommandResult.success(
                outputs={
                    "left": int(rect.get("left", 0)),
                    "top": int(rect.get("top", 0)),
                    "width": int(rect.get("width", 0)),
                    "height": int(rect.get("height", 0)),
                    "count": int(reply.get("count", 0)),
                },
                diagnostics={"usedLayer": "image"},  # ADR 0003 实际使用层证据
            )
        return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, f"unsupported command: {command}")

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        try:
            return await asyncio.to_thread(self._execute_sync, invocation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 执行期异常统一落 EXECUTOR_FAILED
            return CommandResult.failure(ErrorCode.EXECUTOR_FAILED, str(exc))
