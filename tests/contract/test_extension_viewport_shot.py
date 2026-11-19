"""扩展腿的视口图接线（M102 S2b）。

现场：桌面元素入库有截图、网页元素没有。`element_shot_from_descriptor` 可以算得再准，
只要 host 没把扩展给的视口图挂到描述符上，网页元素照样是空位——这一段就是那个落点。

两条判据互为兜底，缺一条都会在真机上表现为「什么都没有」：

1. 有图 ⇒ 挂成 ``captureShot`` **并带 ``space="viewport"``**。标记不是装饰：同一个
   字段还有「窗口图」口径，消费方靠它分辨该用哪套几何；漏了标记，图在、位置全错。
2. 没图（旧扩展 / 无权限 / 页面不可见）⇒ 退回窗口截图。这条是兼容底线：老环境
   原本有窗口截图可用，不能因为引入视口图就退化成没有。
"""

from __future__ import annotations

from rpa_core.capture.extension import ExtensionCaptureSession


def _session() -> ExtensionCaptureSession:
    """只构造不启动：__init__ 只建 Event / 锁，不起线程、不连端点。"""
    return ExtensionCaptureSession()


def test_viewport_shot_is_attached_with_space_marker():
    session = _session()
    descriptor: dict = {"kind": "browser"}
    session._take_viewport_shot(descriptor, "data:image/jpeg;base64,AAA")
    assert descriptor["captureShot"] == {
        "dataUrl": "data:image/jpeg;base64,AAA",
        "space": "viewport",
    }


def test_missing_viewport_shot_falls_back_to_window_capture(monkeypatch):
    """没有视口图时**必须**回到窗口截图：旧扩展环境原本就靠它。"""
    session = _session()
    called: list[dict] = []
    monkeypatch.setattr(session, "_attach_capture_shot", lambda doc: called.append(doc))
    descriptor: dict = {"kind": "browser"}
    session._take_viewport_shot(descriptor, None)
    assert called == [descriptor]
    assert "captureShot" not in descriptor


def test_non_image_payload_is_not_treated_as_viewport_shot(monkeypatch):
    """回来的不是图（降级成一个字符串/错误信息）⇒ 同样退回窗口截图。"""
    session = _session()
    called: list[dict] = []
    monkeypatch.setattr(session, "_attach_capture_shot", lambda doc: called.append(doc))
    descriptor: dict = {"kind": "browser"}
    session._take_viewport_shot(descriptor, "capture failed: no permission")
    assert called == [descriptor]
    assert "captureShot" not in descriptor
