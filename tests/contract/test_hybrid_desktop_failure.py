"""M83：桌面腿出局时，状态文案必须说真话。

2026-11-17 报障的可见性根因：桌面腿的子进程秒退，但扩展腿还活着 ⇒
``_exhausted`` 不成立、会话继续等，浮窗上仍写着「桌面：就绪」。用户按 F9 一直没反应，
唯一线索是一条谁也没看的 trace。这里钉住两件事：

1. ``HybridCaptureSession`` 会把桌面腿的**真故障**记下来（timeout 不算）；
2. ``desktop_status`` 据此报「不可用 + 原因」且 ``ok=False``，而不是「就绪」。
   前缀刻意不写死「起不来」：原因也可能是捕获期内部错误（M85）或「读不到该位置
   的元素」，写死会把用户引去查环境/重装插件。
"""

from __future__ import annotations

import threading

from rpa_core.capture_kit import engine


class _FakeDesktop:
    """桌面腿替身：pick 立刻返回给定结果（不 spawn 子进程）。"""

    available = True

    def __init__(self, result: dict) -> None:
        self._result = result
        self.cancelled = False

    def pick(self, timeout_seconds: float = 0) -> dict:  # noqa: ARG002
        return dict(self._result)

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        pass


class _OfflineExtension:
    """扩展腿替身：离线（不参与竞速），这样桌面腿的失败会立刻收场。"""

    offline = True
    armed = False

    def __init__(self) -> None:
        self.result_event = threading.Event()

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _session(result: dict):
    from rpa_core.capture.hybrid import HybridCaptureSession

    return HybridCaptureSession(
        desktop_factory=lambda **kw: _FakeDesktop(result),  # noqa: ARG005
        extension_session=_OfflineExtension(),
    )


def test_desktop_spawn_failure_is_recorded_and_surfaced() -> None:
    session = _session(
        {
            "timeout": True,
            "error": "desktop capture agent exited with code 1",
            "stderrTail": "ModuleNotFoundError: No module named 'rpa_core'",
        }
    )
    session.start()
    session.pick(timeout_seconds=1.0)

    assert session.desktop_failure is not None
    text, ok = engine.desktop_status(session)
    assert ok is False, "桌面腿崩了还报「就绪」= 用户只能对着浮窗干等"
    assert "不可用" in text
    assert "exited with code 1" in text


def test_plain_timeout_is_not_a_failure() -> None:
    """用户还没按热键是正常等待，浮窗不能因此报红。"""
    session = _session({"timeout": True})
    session.start()
    session.pick(timeout_seconds=1.0)

    assert session.desktop_failure is None
    text, ok = engine.desktop_status(session)
    assert ok is True
    assert "就绪" in text


def test_status_truncates_long_reason() -> None:
    session = _session({"timeout": True, "error": "x" * 200})
    session.start()
    session.pick(timeout_seconds=1.0)

    text, ok = engine.desktop_status(session)
    assert ok is False
    assert len(text) < 80, "浮窗是定宽的一行，超长原因会被截断成看不出重点"
