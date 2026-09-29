"""M47 S1 活体校验通道（ElementVerifier）的契约判据。

通道是按需短连接：请求/结果按 requestId 配对、陈旧回传不认、任何失败都是
结构化 error（绝不挂死、绝不抛给 GUI）。这里的替身通道 monkeypatch
``local_transport.connect`` 与 ``extension_exec.list_extension_endpoints``。
"""

from __future__ import annotations

import threading
import time

import pytest

from rpa_core.capture.verify import ElementVerifier


class FakeChannel:
    """假 bridge 通道：recv **阻塞等待** outbox（与真通道一致——没有消息不是断连），
    close() 才会让 recv 返回 None。outbox 由测试预填或延时填充。"""

    def __init__(self, outbox: list[dict] | None = None):
        self.sent: list[dict] = []
        self.outbox = list(outbox or [])
        self.closed = False
        self._wake = threading.Event()

    def send(self, message: dict) -> None:
        self.sent.append(message)

    def recv(self) -> dict | None:
        while True:
            if self.outbox:
                return self.outbox.pop(0)
            if self.closed:
                return None
            self._wake.wait(timeout=0.05)

    def close(self) -> None:
        self.closed = True
        self._wake.set()


@pytest.fixture()
def verify_env(monkeypatch):
    """装配：返回 (channels, set_endpoints)——测试先建假通道再声明端点名单。"""
    import rpa_core.capture.verify as verify_mod
    import rpa_core.extension_exec as ext_exec

    channels: list[FakeChannel] = []

    def set_endpoints(names: list[str], channels_by_name: dict[str, FakeChannel]):
        def fake_connect(name, timeout=2.0):
            channel = channels_by_name[name]
            channels.append(channel)
            return channel

        monkeypatch.setattr(ext_exec, "list_extension_endpoints", lambda: names)
        monkeypatch.setattr(verify_mod.local_transport, "connect", fake_connect)

    return channels, set_endpoints


def test_verify_returns_count_and_sends_request(verify_env):
    _channels, set_endpoints = verify_env
    good = FakeChannel()
    other = FakeChannel()
    set_endpoints(["a", "b"], {"a": good, "b": other})

    def delayed_reply():
        time.sleep(0.05)
        # requestId 由 verify() 生成，从 send 的请求里回读（配对语义的另一面）
        request_id = good.sent[0]["requestId"]
        good.outbox.append(
            {"type": "capture_verify_result", "requestId": request_id, "count": 3}
        )

    threading.Thread(target=delayed_reply, daemon=True).start()
    result = ElementVerifier(timeout=3.0).verify("div.ok")
    assert result == {"count": 3}
    assert good.closed and other.closed  # 短连接：用完必断
    assert good.sent[0]["type"] == "capture_verify"
    assert good.sent[0]["css"] == "div.ok"


def test_verify_ignores_stale_request_id(verify_env):
    """陈旧/异源回传不认：requestId 不匹配的结果不能被当成答案。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel(
        outbox=[
            {"type": "capture_verify_result", "requestId": "stale", "count": 99},
        ]
    )
    set_endpoints(["a"], {"a": channel})

    def delayed_reply():
        time.sleep(0.05)
        request_id = channel.sent[0]["requestId"]
        channel.outbox.append(
            {"type": "capture_verify_result", "requestId": request_id, "count": 1}
        )

    threading.Thread(target=delayed_reply, daemon=True).start()
    assert ElementVerifier(timeout=3.0).verify("#x") == {"count": 1}


def test_verify_offline_is_structured_error(verify_env):
    _channels, set_endpoints = verify_env
    set_endpoints([], {})
    assert ElementVerifier().verify("#x") == {"error": "extension-offline"}


def test_verify_timeout_is_structured_error_not_hang(verify_env):
    """通道不回话：硬超时报 error，**绝不挂死**（M40 挂住教训）。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()  # 永不填充 outbox、永不 close：真挂住形状
    set_endpoints(["a"], {"a": channel})
    started = time.perf_counter()
    assert ElementVerifier(timeout=0.3).verify("#x") == {"error": "verify-timeout"}
    assert time.perf_counter() - started < 5.0
    assert channel.closed


def test_verify_error_reply_and_bad_count(verify_env):
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})

    def delayed_reply():
        time.sleep(0.05)
        rid = channel.sent[0]["requestId"]
        channel.outbox.append(
            {"type": "capture_verify_result", "requestId": rid, "error": "no-active-tab"}
        )

    threading.Thread(target=delayed_reply, daemon=True).start()
    assert ElementVerifier(timeout=3.0).verify("#x") == {"error": "no-active-tab"}


def test_verify_bad_count_shape_is_error(verify_env):
    """count 不是非负 int（含 bool 混入）：结构化报错，不把垃圾当命中数。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})

    def delayed_reply():
        time.sleep(0.05)
        rid = channel.sent[0]["requestId"]
        channel.outbox.append(
            {"type": "capture_verify_result", "requestId": rid, "count": "many"}
        )

    threading.Thread(target=delayed_reply, daemon=True).start()
    assert ElementVerifier(timeout=3.0).verify("#x") == {"error": "bad-reply"}


# ---- M48：同一通道三个 mode --------------------------------------------------


def _reply_with_count(channel: FakeChannel, count: int) -> None:
    def delayed_reply():
        time.sleep(0.05)
        rid = channel.sent[0]["requestId"]
        channel.outbox.append(
            {"type": "capture_verify_result", "requestId": rid, "count": count}
        )

    threading.Thread(target=delayed_reply, daemon=True).start()


def test_preview_sends_preview_mode(verify_env):
    """preview() 与 verify() 同通道，但信封必须带 mode="preview"。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_count(channel, 2)
    assert ElementVerifier(timeout=3.0).preview("#kw") == {"count": 2}
    assert channel.sent[0]["mode"] == "preview"


def test_verify_sends_flash_mode(verify_env):
    """verify() 显式带 flash（content 侧对缺省信封的兜底不能掩盖显式语义）。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_count(channel, 1)
    assert ElementVerifier(timeout=3.0).verify("#kw") == {"count": 1}
    assert channel.sent[0]["mode"] == "flash"


def test_clear_preview_sends_clear_mode_and_empty_css(verify_env):
    """clear_preview() 只为清场：mode="clear" 且 css 为空（content 侧不查找）。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_count(channel, 0)
    assert ElementVerifier(timeout=3.0).clear_preview() == {"count": 0}
    assert channel.sent[0]["mode"] == "clear"
    assert channel.sent[0]["css"] == ""


def test_bridge_error_reply_fails_fast(verify_env):
    """bridge 拒绝（白名单漏项等）是**对本次请求的应答**：立即透出为 bridge-error，
    不许干等超时——那会把「通道配置错了」伪装成「扩展没响应」（M47.2 真机踩过）。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel(
        outbox=[
            {
                "type": "error",
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "unsupported message type: 'capture_verify'",
                },
            }
        ],
    )
    set_endpoints(["a"], {"a": channel})

    started = time.perf_counter()
    result = ElementVerifier(timeout=5.0).verify("#x")
    elapsed = time.perf_counter() - started
    assert result["error"] == "bridge-error: unsupported message type: 'capture_verify'"
    assert elapsed < 2.0  # 立即返回，而不是 5s 超时
