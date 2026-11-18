"""M75 S2 判据：显式选择守卫 + 禁止静默降级（ADR 0003）。

核心判据：语义空命中时图像后端**调用次数=0**（探针计数，等值断言）；
只有显式声明 driver/backend="image" 才调图像且证据落 usedLayer=image。
"""

from __future__ import annotations

from rpa_core.capture.image_guard import (
    IMAGE_LAYER,
    SEMANTIC_LAYER,
    is_explicit_image,
    resolve_locator_layer,
)


class _Probe:
    """记录调用次数与入参的替身后端。"""

    def __init__(self, result: object = None) -> None:
        self.result = result
        self.calls: list[object] = []

    def __call__(self, request: object) -> object:
        self.calls.append(request)
        return self.result


# ---- 显式选择判定（严格等值）------------------------------------------------


def test_driver_image_is_explicit():
    assert is_explicit_image({"driver": "image"}) is True
    assert is_explicit_image({"backend": "IMAGE"}) is True   # 忽略大小写
    assert is_explicit_image({"driver": " image "}) is True  # strip


def test_semantic_driver_is_not_explicit():
    assert is_explicit_image({"driver": "uia"}) is False
    assert is_explicit_image({"backend": "win32"}) is False
    assert is_explicit_image({}) is False
    assert is_explicit_image("image") is False  # 非 dict 不算


def test_image_related_but_not_driver_is_not_explicit():
    """有图像字段但没显式 driver ⇒ 不算（防后门降级）。"""
    assert is_explicit_image({"imageTemplate": b"x", "minConfidence": 0.8}) is False


# ---- 静默降级红线（核心）----------------------------------------------------


def test_semantic_hit_never_calls_image():
    """语义命中 ⇒ 只调语义，图像调用次数必须=0。"""
    sem = _Probe(result={"hit": 1})
    img = _Probe(result={"hit": 1})
    decision = resolve_locator_layer(
        {"backend": "uia"}, semantic_find=sem, image_find=img
    )
    assert decision.used_layer == SEMANTIC_LAYER
    assert decision.result == {"hit": 1}
    assert len(sem.calls) == 1
    assert len(img.calls) == 0  # 等值，不是「没调或调了也算」


def test_semantic_MISS_never_falls_back_to_image():
    """★ 语义**空命中**时图像后端调用次数也必须=0（ADR 0003 静默降级红线）。"""
    sem = _Probe(result=None)   # UIA 没找到
    img = _Probe(result={"rect": [1, 2, 3, 4]})
    decision = resolve_locator_layer(
        {"backend": "uia"}, semantic_find=sem, image_find=img
    )
    assert decision.used_layer == SEMANTIC_LAYER
    assert decision.result is None
    assert len(sem.calls) == 1
    assert len(img.calls) == 0  # 核心：空命中也绝不降级


def test_semantic_empty_count_zero_never_calls_image():
    """语义回 count=0（另一种空命中形状）同样不降级。"""
    sem = _Probe(result={"count": 0})
    img = _Probe(result={"count": 1})
    decision = resolve_locator_layer({}, semantic_find=sem, image_find=img)
    assert decision.used_layer == SEMANTIC_LAYER
    assert len(img.calls) == 0


def test_explicit_image_calls_image_only():
    """显式 driver=image ⇒ 只调图像，语义调用次数=0。"""
    sem = _Probe(result={"hit": 1})
    img = _Probe(result={"rect": [5, 6, 7, 8]})
    decision = resolve_locator_layer(
        {"driver": "image", "template": b"tpl"}, semantic_find=sem, image_find=img
    )
    assert decision.used_layer == IMAGE_LAYER
    assert decision.result == {"rect": [5, 6, 7, 8]}
    assert len(img.calls) == 1
    assert len(sem.calls) == 0


def test_explicit_backend_image_alias():
    """backend="image" 是 driver="image" 的等价写法。"""
    sem, img = _Probe(), _Probe(result="image-hit")
    decision = resolve_locator_layer(
        {"backend": "image"}, semantic_find=sem, image_find=img
    )
    assert decision.used_layer == IMAGE_LAYER
    assert len(img.calls) == 1 and len(sem.calls) == 0


# ---- 证据记录 ---------------------------------------------------------------


def test_evidence_records_used_layer_for_both():
    """两条路径都把 usedLayer 写进证据（ADR 0003 要求记录实际使用层）。"""
    ev: dict = {}
    resolve_locator_layer(
        {"driver": "uia"}, semantic_find=_Probe(), image_find=_Probe(), evidence=ev
    )
    assert ev["usedLayer"] == SEMANTIC_LAYER
    ev2: dict = {"existing": "kept"}
    resolve_locator_layer(
        {"driver": "image"}, semantic_find=_Probe(), image_find=_Probe(), evidence=ev2
    )
    assert ev2["usedLayer"] == IMAGE_LAYER
    assert ev2["existing"] == "kept"  # 不覆盖其它键


def test_decision_as_evidence_shape():
    d = resolve_locator_layer(
        {"driver": "image"}, semantic_find=_Probe(), image_find=_Probe(result="x")
    )
    assert d.as_evidence() == {"usedLayer": IMAGE_LAYER}
