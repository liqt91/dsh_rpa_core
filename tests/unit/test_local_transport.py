"""本地 IPC 传输层单测（M20 / ADR 0015 S1）。

覆盖：长度前缀帧边界、stdio 流通道、端点服务端/客户端往返、多端点枚举、
连接超时、不存在端点、端点名归一、同进程多实例并存、实例段定长 token 与
路径长度守卫（M22 macOS 真机修复）、残留端点回收。
"""

from __future__ import annotations

import io
import json
import os
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

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


def test_endpoint_name_uses_fixed_length_instance_token():
    """实例段必须是定长 token（M22：原始 UUID 直接拼名字会顶穿 sun_path 上限）。"""
    instance_id = "a6ce2631-f8db-4c1c-93a8-3f53f8fe2058"  # 浏览器生成的 UUID（36 字符）
    token = lt.instance_token(instance_id)
    assert lt.endpoint_name("msedge", instance_id, prefix="rpa_core_ext_") == (
        f"rpa_core_ext_msedge_{token}"
    )
    assert len(token) == 16
    assert instance_id not in lt.endpoint_name("msedge", instance_id)


def test_instance_token_is_stable_and_content_independent():
    long_id = "x" * 300
    tokens = [
        lt.instance_token(value)
        for value in ("a", "abc-123", long_id, "a6ce2631-f8db-4c1c-93a8-3f53f8fe2058")
    ]
    assert all(len(token) == lt._INSTANCE_TOKEN_CHARS for token in tokens)
    assert len(set(tokens)) == len(tokens)  # 不同 instanceId 不同 token
    assert lt.instance_token(long_id) == lt.instance_token(long_id)  # 稳定
    assert lt.instance_token("") == "default"


def test_endpoint_name_normalizes_illegal_chars():
    assert lt.endpoint_name("", "", prefix="rpa_core_ext_") == (
        "rpa_core_ext_unknown_default"
    )
    assert " " not in lt.endpoint_name("my browser", "id with spaces")
    assert "/" not in lt.endpoint_name("a/b", "c\\d")


# -- 路径长度（M22 真机修复回归）------------------------------------------------


@pytest.mark.skipif(lt._IS_WINDOWS, reason="AF_UNIX 路径上限仅 POSIX")
def test_endpoint_path_fits_sun_path_limit_for_browser_uuid():
    path = lt.endpoint_path(lt.endpoint_name("msedge", str(uuid.uuid4())))
    assert path is not None
    assert len(os.fsencode(path)) <= lt._POSIX_PATH_MAX
    if sys.platform == "darwin":
        # 回归对照：老实现（61 字节 per-user TMPDIR + 36 字符 UUID）在 macOS 上是
        # 123 字节 —— 必然 bind 失败，真机表现为「插件永远离线」
        legacy = len(
            os.fsencode(
                Path(tempfile.gettempdir())
                / "rpa_core_ext"
                / f"rpa_core_ext_msedge_{uuid.uuid4()}.sock"
            )
        )
        assert legacy > lt._POSIX_PATH_MAX


@pytest.mark.skipif(lt._IS_WINDOWS, reason="POSIX 端点目录")
def test_endpoint_dir_is_short_and_private():
    directory = lt.endpoint_dir()
    # macOS 下必须是短目录（/tmp），不能是 61 字节的 /var/folders/… per-user 临时区
    assert len(os.fsencode(directory)) <= 40
    assert b"/var/folders" not in os.fsencode(directory)
    info = directory.lstat()
    assert stat.S_ISDIR(info.st_mode)
    assert not directory.is_symlink()
    assert info.st_uid == os.getuid()
    assert info.st_mode & 0o077 == 0


@pytest.mark.skipif(lt._IS_WINDOWS, reason="POSIX 长度守卫")
def test_endpoint_path_guard_reports_clear_error(monkeypatch):
    monkeypatch.setenv("RPA_EXT_ENDPOINT_PREFIX", "rpa_core_ext_" + "x" * 80)
    name = lt.endpoint_name("msedge", "guard-probe")
    with pytest.raises(lt.LocalTransportError) as excinfo:
        lt.endpoint_path(name)
    message = str(excinfo.value)
    assert "too long" in message
    assert str(lt._POSIX_PATH_MAX) in message
    # bind 侧同样必须抛领域异常：此前抛的是裸 OSError，调用方的 except 是死代码
    with pytest.raises(lt.LocalTransportError):
        lt.LocalEndpointServer(name)


@pytest.mark.skipif(lt._IS_WINDOWS, reason="残留 socket 回收仅 POSIX")
def test_prune_stale_endpoints_removes_dead_and_keeps_live():
    live_name = lt.endpoint_name("msedge", "prune-live")
    live_path = lt.endpoint_path(live_name)
    assert live_path is not None
    dead_name = lt.endpoint_name("msedge", "prune-dead")
    dead_path = lt.endpoint_path(dead_name)
    assert dead_path is not None
    server = _server(live_name)
    try:
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(dead_path))  # 只 bind 不 listen：文件残留且连不上
        stale.close()
        old = time.time() - lt._STALE_ENDPOINT_AGE_SECONDS - 60
        os.utime(dead_path, (old, old))
        os.utime(live_path, (old, old))  # 活端点即使 mtime 很老也必须保留
        removed = lt.prune_stale_endpoints()
        assert dead_name in removed
        assert not dead_path.exists()
        assert live_name not in removed
        assert live_path.exists()
        assert live_name in lt.list_endpoints()
    finally:
        server.close()
    _wait_until(lambda: live_name not in lt.list_endpoints())


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


@pytest.mark.skipif(lt._IS_WINDOWS, reason="AF_UNIX socketpair 仅 POSIX")
def test_closed_socket_channel_raises_domain_error():
    """通道关闭后 send/recv 必须抛领域异常，不得以裸 OSError 逃逸。

    回归（macOS 真机 2026-09-18）：``capture.extension._read_loop`` 只捕
    ``LocalTransportError``，而通道在别处被 close 后 ``recv()`` 抛
    ``OSError: [Errno 9] Bad file descriptor`` → 逃逸成线程未捕获异常。
    领域边界（``_SocketChannel``）负责收口，调用方的 except 才有意义。
    """
    left, right = socket.socketpair()
    channel = lt._SocketChannel(left)
    right.close()
    left.close()  # 模拟「通道已被别处关闭」
    with pytest.raises(lt.LocalTransportError):
        channel.recv()
    with pytest.raises(lt.LocalTransportError):
        channel.send({"type": "capture_disarm"})


def test_transport_error_is_not_an_oserror():
    """领域异常不得继承 OSError：否则领域边界的 ``except OSError`` 会自吞。"""
    assert not issubclass(lt.LocalTransportError, OSError)


def test_isolated_endpoint_prefix_does_not_overlap_default_namespace():
    """测试隔离前缀与默认前缀必须互不包含（双向）。

    回归（2026-09-18 跨进程实测取证）：早先的隔离前缀
    ``rpa_core_ext_test_isolated_`` 以默认前缀 ``rpa_core_ext_`` 开头，而
    ``list_endpoints`` 用 ``startswith`` 过滤 —— 于是**真实进程能列出测试端点**。
    后果不止「门禁结论漂移」：开发者跑测试期间用 GUI/CLI 捕获，会 arm 到测试的
    假 bridge 端点、拿到测试描述符并当作真实元素落库（本机复现：真机捕获验证
    脚本被测试端点接管，返回了测试夹具里的 ``#go`` 描述符）。
    """
    default = lt._ENDPOINT_PREFIX
    effective = lt.endpoint_prefix()
    if effective == default:
        pytest.skip("未设隔离前缀（非测试上下文）")
    assert not effective.startswith(default), (
        f"隔离前缀 {effective!r} 以默认前缀 {default!r} 开头："
        "真实进程的 list_endpoints() 会串到测试端点"
    )
    assert not default.startswith(effective), (
        f"默认前缀 {default!r} 以隔离前缀 {effective!r} 开头："
        "测试进程会串到本机真实端点"
    )
