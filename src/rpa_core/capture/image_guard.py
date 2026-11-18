"""显式选择守卫（M75 S2）：禁止 UIA→坐标静默降级（ADR 0003 硬约束）。

ADR 0003 分层边界原文：「禁止从 UIA 静默降级到坐标点击。降级必须由 workflow 明确
选择不同 locator/driver，并在证据中记录实际使用层。」

本模块是这条铁律的**唯一守卫**：
* 只有请求**显式声明** ``driver="image"`` / ``backend="image"`` 才允许走图像后端；
* 语义（UIA/win32）空命中时**绝不**自动调图像——这是 ADR 0003 明确禁止的静默降级；
* 每次解析都把实际使用层写进证据 ``usedLayer``，供运行记录/诊断消费。

它**不实现定位**：语义查找与图像查找都是注入的 callable，守卫只决定「调哪一个」，
不碰 S1 的 ``image_locator`` 实现，也不碰 ``DesktopExecutor._find``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

IMAGE_DRIVER = "image"
SEMANTIC_LAYER = "semantic"
IMAGE_LAYER = "image"

#: 图像模板 locator 形状版本（M80）。V1 没有 DPI provenance ⇒ 显式拒绝，不迁移。
TEMPLATE_SCHEMA = 2

# 显式声明图像驱动的键（都认，避免两种写法各说各话）
_DRIVER_KEYS = ("driver", "backend")


@dataclass(frozen=True)
class LayerDecision:
    """解析决定：用了哪一层 + 图像命中结果（若走图像）。"""

    used_layer: str
    result: Any = None

    def as_evidence(self) -> dict[str, Any]:
        """落进证据/诊断的形状。"""
        return {"usedLayer": self.used_layer}


def is_explicit_image(request: Any) -> bool:
    """请求是否**显式**声明走图像驱动。

    严格等值：只有 ``driver``/``backend`` 字段的值恰好是 ``"image"``（忽略大小写）才算。
    「有 image 字段」「语义没找到」都**不算**显式选择——防止静默降级从后门溜进来。
    """
    if not isinstance(request, dict):
        return False
    for key in _DRIVER_KEYS:
        value = request.get(key)
        if isinstance(value, str) and value.strip().casefold() == IMAGE_DRIVER:
            return True
    return False


def image_locator_errors(request: Any) -> list[str]:
    """显式 image locator 的形状校验（模板 V2 / 文本路径各判各的）。

    - 非显式请求：直接拒绝（调用方一般已先判过，这里再守一次防绕过）；
    - ``imageTemplate`` 路径：必须 ``templateSchema==2`` 且 ``templateSize`` 为正整数对；
      V1 模板**不自动兼容**，必须重新捕获（开发期不做数据迁移的约定）；
    - ``imageKeyword`` 路径：不需要 DPI provenance，不套模板版本。
    """
    if not isinstance(request, dict):
        return ["locator must be an object"]
    if not is_explicit_image(request):
        return ["image locator must be explicit"]
    if not request.get("imageTemplate"):
        return []
    errors: list[str] = []
    if request.get("templateSchema") != TEMPLATE_SCHEMA:
        errors.append("template-schema-requires-recapture")
    size = request.get("templateSize")
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)
    ):
        errors.append("template-size-must-be-positive-pair")
    return errors


def resolve_locator_layer(
    request: Any,
    *,
    semantic_find: Callable[[Any], Any],
    image_find: Callable[[Any], Any],
    evidence: dict[str, Any] | None = None,
) -> LayerDecision:
    """按「是否显式选 image」分派到语义或图像后端，并记录使用层。

    - 显式 image ⇒ 调 ``image_find(request)``，``used_layer="image"``；
    - 否则 ⇒ 只调 ``semantic_find(request)``，``used_layer="semantic"``；
      **即便语义返回空命中也绝不调 ``image_find``**（ADR 0003 静默降级红线）。
    - ``evidence`` 若传入，写入 ``usedLayer``（不改动其它键）。

    ``semantic_find`` / ``image_find`` 是注入点：测试用计数替身证明「谁被调用、调用几次」。
    """
    explicit = is_explicit_image(request)
    if explicit:
        result = image_find(request)
        decision = LayerDecision(used_layer=IMAGE_LAYER, result=result)
    else:
        # 语义空命中也到此为止——绝不 fall through 到 image_find
        result = semantic_find(request)
        decision = LayerDecision(used_layer=SEMANTIC_LAYER, result=result)

    if evidence is not None:
        evidence["usedLayer"] = decision.used_layer
    return decision
