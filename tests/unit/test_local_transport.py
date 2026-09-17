"""本地 IPC 传输层单测（M20 / ADR 0015 S1）。

覆盖：长度前缀帧边界、stdio 流通道、端点服务端/客户端往返、多端点枚举、
连接超时、不存在端点、端点名归一、同进程多实例并存。
"""

from __future__ import annotations

import io
import json
import struct
import threading
import time

import pytest

from rpa_core import local_transport as lt

# -- 帧编码 --------------------------------------------------------------------


def test_encode_decode_roundtrip():
    payload = {"type": "hello", "browser": "msedge", "中文": "值"}
    encoded = lt.encode_message(payload)
    assert struct.unpack("<I", encoded[:4])[0] == len(encoded) - 4
    assert json.loads(encoded[4:].decode("utf-8")) == payload


def test_encode_rejects_oversized():
    huge = {"data": "x" * (lt.MAX_MESSAGE_BYTES + 1)}
    with pytest.raises(lt.LocalTransportError):
        lt.encode_message(huge)


def test_stream_channel_roundtrip():
    buffer = io.BytesIO()
    writer = lt.StreamChannel(io.BytesIO(b""), buffer)
    writer.send({"a": 1})
    writer.send({"b": [1, 2, 3]})
    reader = lt.StreamChannel(io.BytesIO(buffer.getvalue()), io.BytesIO(b""))
    assert reader.recv() == {"a": 1}
    assert reader.recv() == {"b": [1, 2, 3]}
    assert reader.recv() is None  # 干净 EOF


def test_stream_read_rejects_truncated_header():
    reader = lt.StreamChannel(io.BytesIO(b"\x02\x00"), io.BytesIO(b""))
    with pytest.raises(lt.LocalTransportError):
        reader.recv()


def test_stream_read_rejects_truncated_body():
    frame = struct.pack("<I", 10) + b"short"
    reader = lt.StreamChannel(io.BytesIO(frame), io.BytesIO(b""))
    with pytest.raises(lt.LocalTransportError):
        reader.recv()


def test_stream_read_rejects_declared_oversize():
    frame = struct.pack("<I", lt.MAX_MESSAGE_BYTES + 1)
    reader = lt.StreamChannel(io.BytesIO(frame), io.BytesIO(b""))
    with pytest.raises(lt.LocalTransportError):
        reader.recv()


def test_stream_read_rejects_non_object_payload():
    body = b"[1, 2]"
    reader = lt.StreamChannel(
        io.BytesIO(struct.pack("<I", len(body)) + body), io.BytesIO(b"")
    )
    with pytest.raises(lt.LocalTransportError):
        reader.recv()


# -- 端点命名 ------------------------------------------------------------------


def test_endpoint_name_normalizes_illegal_chars():
    assert (
        lt.endpoint_name("msedge", "abc-123", prefix="rpa_core_ext_")
        == "rpa_core_ext_msedge_abc-123"
    )
    assert lt.endpoint_name("", "", prefix="rpa_core_ext_") == (
        "rpa_core_ext_unknown_default"
    )
    assert " " not in lt.endpoint_name("my browser", "id with spaces")
    assert "/" not in lt.endpoint_name("a/b", "c\\d")


# -- 端点往返 ------------------------------------------------------------------


def _server(name: str) -> lt.LocalEndpointServer:
    return lt.LocalEndpointServer(name)


def test_endpoint_roundtrip():
    name = lt.endpoint_name("msedge", "unit-roundtrip")
    server = _server(name)
    try:
        client_box: dict = {}

        def client():
            client_box["channel"] = lt.connect(name, timeout=3.0)

        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        channel = server.accept(timeout=5.0)
        assert channel is not None
        thread.join(timeout=3)
        with client_box["channel"] as client_channel:
            client_channel.send({"type": "hello", "instanceId": "unit-roundtrip"})
            assert channel.recv() == {"type": "hello", "instanceId": "unit-roundtrip"}
            channel.send({"type": "result", "ok": True})
            assert client_channel.recv() == {"type": "result", "ok": True}
            client_channel.close()
            assert channel.recv() is None  # 对端关闭 → 干净 EOF
        channel.close()
    finally:
        server.close()


def test_endpoint_listed_and_removed_on_close():
    name = lt.endpoint_name("chrome", "unit-listing")
    server = _server(name)
    try:
        assert name in lt.list_endpoints()
    finally:
        server.close()
    _wait_until(lambda: name not in lt.list_endpoints())


def test_connect_missing_endpoint_raises():
    with pytest.raises(lt.LocalTransportError):
        lt.connect(lt.endpoint_name("msedge", "unit-missing"), timeout=0.3)


def test_accept_timeout_returns_none():
    server = _server(lt.endpoint_name("msedge", "unit-accept-timeout"))
    try:
        started = time.monotonic()
        assert server.accept(timeout=0.2) is None
        assert time.monotonic() - started < 3.0
    finally:
        server.close()


def test_multiple_endpoints_coexist():
    names = [lt.endpoint_name("msedge", f"unit-multi-{i}") for i in range(3)]
    servers = [_server(name) for name in names]
    try:
        listed = lt.list_endpoints()
        assert all(name in listed for name in names)
    finally:
        for server in servers:
            server.close()


def test_multiple_clients_sequential():
    name = lt.endpoint_name("chrome", "unit-sequential")
    server = _server(name)
    try:
        for index in range(3):
            box: dict = {}

            def client(i=index, target=box):
                target["channel"] = lt.connect(name, timeout=3.0)

            thread = threading.Thread(target=client, daemon=True)
            thread.start()
            channel = server.accept(timeout=5.0)
            assert channel is not None
            thread.join(timeout=3)
            with box["channel"] as client_channel:
                client_channel.send({"index": index})
                assert channel.recv() == {"index": index}
            channel.close()
    finally:
        server.close()


def test_double_bind_same_endpoint_is_platform_defined():
    """同名端点二次绑定：Windows 管道允许多实例（供多客户端），POSIX 活端点必须拒绝。"""
    name = lt.endpoint_name("msedge", "unit-double-bind")
    server = _server(name)
    try:
        if lt._IS_WINDOWS:
            second = _server(name)
            second.close()
        else:
            with pytest.raises(lt.LocalTransportError):
                _server(name)
    finally:
        server.close()


@pytest.mark.skipif(not lt._IS_WINDOWS, reason="命名管道仅 Windows")
def test_connect_detects_client_that_connected_before_connect_named_pipe():
    """回归：客户端在 ConnectNamedPipe 之前接入时，连接阶段必须识别为已连接。

    实测竞态：客户端若在 CreateNamedPipe 与 ConnectNamedPipe 之间接入，overlapped
    的 connect 返回 pending 且**永不完成**——曾导致第二个客户端永远不被 accept
    （表现为并发客户端丢回复）。修法是 PeekNamedPipe 兜底。
    """
    name = lt.endpoint_name("msedge", "unit-pre-connect")
    server = _server(name)
    try:
        impl = server._impl
        first = lt.connect(name, timeout=3.0)  # 占掉 __init__ 预建的实例
        handle = impl._create_instance()  # 新实例：尚未 ConnectNamedPipe
        second = lt.connect(name, timeout=3.0)  # 只能接入该实例（竞态窗口）
        _, _, connected = impl._connect_instance(handle)
        assert connected is True
        second.close()
        first.close()
    finally:
        server.close()


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
