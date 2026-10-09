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


def endpoint_for(browser: str, token: str) -> str:
    """按**当前生效前缀**构造端点名（测试套件把前缀改成 ``rpacore-iso_`` 隔离，
    写死的 ``rpa_core_ext_`` 会让 ``_endpoint_browser`` 解析不出浏览器段）。"""
    from rpa_core.local_transport import endpoint_prefix

    return f"{endpoint_prefix()}{browser}_{token}"


@pytest.fixture()
def verify_env(monkeypatch):
    """装配：返回 (channels, set_endpoints)——测试先建假通道再声明端点名单。

    **默认把 Z 序自动目标打桩为「无浏览器」**：本夹具的端点名是 ``"a"``/``"b"`` 这类
    假名，与 ``rpa_core_ext_<browser>_<token>`` 命名不兼容。若真去问 Z 序
    （``first_browser_window``），结果会随运行机器上开着什么浏览器而变（测试不确定）。
    要测自动目标请用 :func:`foreground_env` 显式打桩。
    """
    import rpa_core.capture.foreground_window as fg_mod
    import rpa_core.capture.verify as verify_mod
    import rpa_core.extension_exec as ext_exec

    channels: list[FakeChannel] = []

    monkeypatch.setattr(fg_mod, "first_browser_window", lambda api=None: None)

    def set_endpoints(names: list[str], channels_by_name: dict[str, FakeChannel]):
        def fake_connect(name, timeout=2.0):
            channel = channels_by_name[name]
            channels.append(channel)
            return channel

        monkeypatch.setattr(ext_exec, "list_extension_endpoints", lambda: names)
        monkeypatch.setattr(verify_mod.local_transport, "connect", fake_connect)

    return channels, set_endpoints


@pytest.fixture()
def foreground_env(monkeypatch):
    """装配（带 Z 序目标）：返回 (channels, set_endpoints, set_targets)。

    ``set_targets(browser, hwnd)`` 打桩 :func:`first_browser_window` 返回该浏览器窗口；
    端点名用真实命名 ``rpa_core_ext_<browser>_<token>`` 以便验证定点过滤。
    """
    import rpa_core.capture.foreground_window as fg_mod
    import rpa_core.capture.verify as verify_mod
    import rpa_core.extension_exec as ext_exec

    channels: list[FakeChannel] = []
    front_calls: list[int] = []

    def set_targets(browser: str, hwnd: int):
        monkeypatch.setattr(
            fg_mod,
            "first_browser_window",
            lambda api=None: fg_mod.ForegroundBrowser(
                hwnd=hwnd, browser=browser, process=f"{browser}.exe", title="t"
            ),
        )
        monkeypatch.setattr(
            fg_mod, "bring_to_foreground", lambda h, api=None: front_calls.append(h) or True
        )

    def set_endpoints(names: list[str], channels_by_name: dict[str, FakeChannel]):
        def fake_connect(name, timeout=2.0):
            channel = channels_by_name[name]
            channels.append(channel)
            return channel

        monkeypatch.setattr(ext_exec, "list_extension_endpoints", lambda: names)
        monkeypatch.setattr(verify_mod.local_transport, "connect", fake_connect)

    return channels, set_endpoints, set_targets, front_calls


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


# ---- M47.9 目标选择（对齐影刀「最近激活的浏览器」）-------------------------------


def test_auto_target_dispatches_only_to_zorder_browser(foreground_env):
    """**本 bug 的核心回归钉**：Z 序识别出 msedge 时，只发给 Edge 端点，
    不再广播给 Chrome——否则两个浏览器都会闪框（M47.8 的老症状）。"""
    channels, set_endpoints, set_targets, _front = foreground_env
    edge = FakeChannel()
    chrome = FakeChannel()
    edge_name, chrome_name = endpoint_for("msedge", "aaa"), endpoint_for("chrome", "bbb")
    set_endpoints([edge_name, chrome_name], {edge_name: edge, chrome_name: chrome})
    set_targets("msedge", hwnd=4242)
    _reply_with_count(edge, 1)

    assert ElementVerifier(timeout=3.0).verify("#kw") == {"count": 1}
    # 只连了 Edge：Chrome 通道从没被建过（不是建了又不用）
    assert channels == [edge]
    assert edge.sent[0]["css"] == "#kw"


def test_explicit_target_filters_matching_endpoint(verify_env):
    """显式 target（如捕获时记下的浏览器名）只发匹配端点。"""
    channels, set_endpoints = verify_env
    edge = FakeChannel()
    chrome = FakeChannel()
    edge_name, chrome_name = endpoint_for("msedge", "aaa"), endpoint_for("chrome", "bbb")
    set_endpoints([edge_name, chrome_name], {edge_name: edge, chrome_name: chrome})
    _reply_with_count(chrome, 5)

    assert ElementVerifier(target="chrome", timeout=3.0).verify("#x") == {"count": 5}
    assert channels == [chrome]


def test_target_not_installed_falls_back_to_all(verify_env):
    """Z 序识别到 msedge 但该浏览器没装/没连扩展：退回全部端点，绝不「点了没反应」。"""
    channels, set_endpoints = verify_env
    chrome = FakeChannel()
    chrome_name = endpoint_for("chrome", "bbb")
    set_endpoints([chrome_name], {chrome_name: chrome})
    _reply_with_count(chrome, 2)

    v = ElementVerifier(target="msedge", timeout=3.0)
    assert v.verify("#x") == {"count": 2}
    assert channels == [chrome]  # 虽然 target 是 msedge，但只能发 Chrome（唯一在线）


def test_no_browser_keeps_broadcast(verify_env):
    """Z 序里没有浏览器（全是资源管理器/桌面）时保持旧的「全部端点」广播。"""
    channels, set_endpoints = verify_env
    a = FakeChannel()
    b = FakeChannel()
    set_endpoints(["a", "b"], {"a": a, "b": b})
    _reply_with_count(a, 1)

    assert ElementVerifier(timeout=3.0).verify("#x") == {"count": 1}
    assert a in channels and b in channels  # 两个都发了（广播兜底）


def test_bring_front_called_for_flash_not_preview_or_clear(foreground_env):
    """影刀那步②：**只有**用户明确点的 ``flash`` 会把目标浏览器置前；
    ``preview``（编辑器边打字边预览）与 ``clear``（收场清场）都不该抢焦点。"""
    _channels, set_endpoints, set_targets, front_calls = foreground_env
    edge = FakeChannel()
    edge_name = endpoint_for("msedge", "aaa")
    set_endpoints([edge_name], {edge_name: edge})
    set_targets("msedge", hwnd=4242)

    _reply_with_count(edge, 1)
    ElementVerifier(timeout=3.0).verify("#kw")          # flash
    assert front_calls == [4242]

    front_calls.clear()
    _reply_with_count(edge, 2)
    ElementVerifier(timeout=3.0).preview("#kw")         # preview
    assert front_calls == []                            # 不抢焦点

    front_calls.clear()
    _reply_with_count(edge, 0)
    ElementVerifier(timeout=3.0).clear_preview()        # clear
    assert front_calls == []


def test_bring_front_disabled(foreground_env):
    """``bring_front=False`` 时不打扰用户焦点（供不想要置前行为的调用方）。"""
    _channels, set_endpoints, set_targets, front_calls = foreground_env
    edge = FakeChannel()
    edge_name = endpoint_for("msedge", "aaa")
    set_endpoints([edge_name], {edge_name: edge})
    set_targets("msedge", hwnd=4242)
    _reply_with_count(edge, 1)

    ElementVerifier(bring_front=False, timeout=3.0).verify("#kw")
    assert front_calls == []


def test_bring_front_failure_is_harmless(monkeypatch, foreground_env):
    """置前失败（Windows 前台锁定）不冒泡：高亮照发，只是可能被别的窗口挡住。"""
    import rpa_core.capture.foreground_window as fg_mod

    _channels, set_endpoints, _set_targets, _front = foreground_env
    edge = FakeChannel()
    edge_name = endpoint_for("msedge", "aaa")
    set_endpoints([edge_name], {edge_name: edge})
    # 让 Z 序识别成功、且 bring_to_foreground 抛异常——_exchange 不该被它带崩
    monkeypatch.setattr(
        fg_mod,
        "first_browser_window",
        lambda api=None: fg_mod.ForegroundBrowser(
            hwnd=4242, browser="msedge", process="msedge.exe", title="t"
        ),
    )

    def boom(hwnd, api=None):
        raise RuntimeError("foreground denied")

    monkeypatch.setattr(fg_mod, "bring_to_foreground", boom)
    _reply_with_count(edge, 3)

    assert ElementVerifier(timeout=3.0).verify("#kw") == {"count": 3}
