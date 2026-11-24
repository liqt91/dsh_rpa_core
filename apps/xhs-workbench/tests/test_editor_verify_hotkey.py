"""元素编辑框的 F10 全局校验热键。

场景：鼠标要悬停在目标网页上才能触发页面内 DOM 显示，点不到「校验元素」按钮。
后端走系统级 GetAsyncKeyState（与捕获 F9 同机制）；前端用**长轮询**而不是
setInterval —— PPX 窗口被浏览器盖住时 Chromium 会把定时器降频，那是「按 F10
没反应」的第一嫌疑。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
GUI = ROOT / "apps" / "editor" / "gui"
MAIN_JS = GUI / "src" / "main.js"
INDEX_HTML = GUI / "index.html"


def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


def _wait_for_fired(expected: int, timeout: float = 1.0) -> int:
    deadline = time.monotonic() + timeout
    fired = 0
    while time.monotonic() < deadline:
        fired = int(_call("rpa.pollVerifyHotkey")["fired"])
        if fired >= expected:
            return fired
        time.sleep(0.03)
    return fired


def test_hotkey_session_counts_rising_edges_and_stops(monkeypatch) -> None:
    api = pb._load_api()
    monkeypatch.setattr(api, "_verify_hotkey_pressed", lambda: False)

    first = _call("rpa.startVerifyHotkey")
    assert first["ok"] is True and first["active"] is True
    assert first["fired"] == 0 and first["sessionId"]
    # 重复 arm 幂等：不能起第二个监听线程把一次按键算两遍
    again = _call("rpa.startVerifyHotkey")
    assert again["sessionId"] == first["sessionId"]

    try:
        assert _call("rpa.pollVerifyHotkey")["fired"] == 0
        monkeypatch.setattr(api, "_verify_hotkey_pressed", lambda: True)
        assert _wait_for_fired(1) >= 1
        # 按住不放不再计次（上升沿语义）
        held = int(_call("rpa.pollVerifyHotkey")["fired"])
        time.sleep(0.15)
        assert int(_call("rpa.pollVerifyHotkey")["fired"]) == held

        monkeypatch.setattr(api, "_verify_hotkey_pressed", lambda: False)
        time.sleep(0.09)
        assert _call("rpa.pollVerifyHotkey")["fired"] == held
    finally:
        _call("rpa.stopVerifyHotkey")

    after = _call("rpa.pollVerifyHotkey")
    assert after["active"] is False and after["fired"] == 0


def test_await_hotkey_blocks_until_edge_or_timeout(monkeypatch) -> None:
    """长轮询的两个端点：没按键要等满预算；按键要**当场**醒，不能等满。"""
    api = pb._load_api()
    original = api._verify_hotkey_pressed
    monkeypatch.setattr(api, "_verify_hotkey_pressed", lambda: False)
    _call("rpa.startVerifyHotkey")
    try:
        started = time.monotonic()
        quiet = _call("rpa.awaitVerifyHotkey", since=0, timeout_ms=300)
        waited = time.monotonic() - started
        assert quiet["fired"] == 0 and quiet["active"] is True
        assert waited >= 0.25, f"没等满预算就返回了：{waited:.3f}s（会变成空转）"

        def press() -> None:
            api._verify_hotkey_pressed = lambda: True

        timer = threading.Timer(0.12, press)
        timer.start()
        started = time.monotonic()
        hit = _call("rpa.awaitVerifyHotkey", since=0, timeout_ms=2000)
        woke = time.monotonic() - started
        timer.cancel()
        assert hit["fired"] >= 1, hit
        # 按键在 0.12s 落下；唤醒要「当场」，不能靠 0.5s 的分片自然醒
        assert woke < 0.35, f"按键后还要 {woke:.3f}s 才返回 ⇒ 挂着的等待没被唤醒"
    finally:
        monkeypatch.setattr(api, "_verify_hotkey_pressed", original)
        _call("rpa.stopVerifyHotkey")


def test_dialog_wires_global_hotkey() -> None:
    src = MAIN_JS.read_text(encoding="utf-8")
    for rpc in ("rpa.startVerifyHotkey", "rpa.awaitVerifyHotkey", "rpa.stopVerifyHotkey"):
        assert f"ppx.call('{rpc}'" in src, f"main.js 没接 {rpc}"
    assert "if (options.mode !== 'detail') armVerifyHotkey()" in src
    assert "function closeElemDialog() {\n  disarmVerifyHotkey()" in src

    # 三条触发路（长轮询 / 兜底定时器 / 窗口键盘）都过同一个入口：
    # 去重、以及「详情模式按钮禁用时不越权」只写在那一处
    assert src.count("triggerHotkeyVerify(") >= 4
    assert "if (!el.dialogValidate || el.dialogValidate.disabled) return false" in src
    # 主路径必须是长轮询；setInterval 只做老后端/替身的兜底
    assert "await ppx.call('rpa.awaitVerifyHotkey'" in src
    assert "setInterval(pollVerifyHotkey, 200)" in src
    # 窗口有焦点时的退路（系统热键读不到时还有这一条）
    assert "event.key !== 'F10'" in src

    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "校验元素（F10）" in html
    assert "按 F10 校验" in html
    # 热键到底有没有接管，要有可见状态，不能靠猜
    assert 'id="elemHotkeyHint"' in html
