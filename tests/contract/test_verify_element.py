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


# ---- M47.11 预览截图回传（wantShot 链路）------------------------------------


def _reply_with_shot(
    channel: FakeChannel,
    *,
    data_url: str | None = "data:image/png;base64,AAAA",
    rect: dict | None = None,
    viewport: dict | None = None,
    count: int = 2,
    window_handle: int | None = 4242,
) -> None:
    """回一条带截图字段的校验结果（rect/viewport 缺省给一组正常值）。

    **rect/viewport 默认给真值而不是省略**：判据要证明「字段被透传了」，若默认就是
    None，漏传字段的 bug 也会因为「回传里本来就没」而假绿。

    M47.12：截图改由 host 统一走桌面坐标截屏，扩展侧**不再回传 dataUrl**——它只回
    ``windowHandle``（截图要窗口绝对矩形，那是 host 的活）。``data_url`` 参数保留且
    默认仍给值，是为了钉住「即使扩展回了 base64，host 也不透传」（扩展版本不对
    或信封被改过时不该把几百 KB 揣进 GUI 内存）。
    """

    def delayed_reply():
        time.sleep(0.05)
        rid = channel.sent[0]["requestId"]
        payload = {"type": "capture_verify_result", "requestId": rid, "count": count}
        if data_url is not None:
            payload["dataUrl"] = data_url
        if rect is not None:
            payload["rect"] = rect
        if viewport is not None:
            payload["viewport"] = viewport
        if window_handle is not None:
            payload["windowHandle"] = window_handle
        channel.outbox.append(payload)

    threading.Thread(target=delayed_reply, daemon=True).start()


RECT = {"left": 100, "top": 50, "width": 200, "height": 80}
VIEWPORT = {
    "width": 1280,
    "height": 720,
    "dpr": 2,
    "scrollX": 300,
    "scrollY": 900,
    # M47.12：坐标截屏要靠视口的**屏幕原点**换算红框（host 侧推不出标签栏高度）。
    "screenX": 12,
    "screenY": 84,
    "outerWidth": 1296,
    "outerHeight": 900,
}


def test_want_shot_true_sends_flag_and_returns_payload(verify_env):
    """要图时：信封带 ``wantShot:true``（严格布尔），结果里 ``windowHandle``/rect/viewport 全透传。

    **M47.12 的关键形状变化**：截图改由 host 统一走桌面坐标截屏，扩展侧不再回传
    ``dataUrl``（几百 KB base64 不该穿过扩展回传通道），只回 ``windowHandle``。
    同一函数刻意仍回一个 dataUrl，用来钉住 host 会把它**丢掉**。
    """
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_shot(channel, rect=dict(RECT), viewport=dict(VIEWPORT))

    result = ElementVerifier(timeout=3.0).verify("#kw", want_shot=True)
    assert channel.sent[0]["wantShot"] is True
    assert result["count"] == 2
    assert result["windowHandle"] == 4242
    assert result["rect"] == RECT
    assert result["viewport"] == VIEWPORT
    # 扩展回传里的 base64 **不**透传（截图由 host 自己截）。
    assert "dataUrl" not in result


def test_default_verify_does_not_ask_for_shot(verify_env):
    """默认（普通「校验元素」）**不**要图：信封wantShot:false，且扩展回传里即使
    带了 dataUrl 也**不**透传——没人要的 几百 KB base64 不该进 GUI 内存。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    # 扩展侧不甩wantShot 就不该有 dataUrl；这里**故意**回一张，钉住 host 的丢弃行为
    _reply_with_shot(channel, rect=dict(RECT), viewport=dict(VIEWPORT))

    result = ElementVerifier(timeout=3.0).verify("#kw")
    assert channel.sent[0]["wantShot"] is False
    assert "dataUrl" not in result
    assert result["count"] == 2  # 命中数不受影响


def test_want_shot_failure_still_returns_count(verify_env):
    """截图拍不到（无 dataUrl）：**count 照常**、不报 error——截图是观感增强，
    命中数才是判据。这是最容易写坏的一条：一旦把截图失败当校验失败，预览页签在
    受保护页面/超大页上就会显示「校验失败」。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_shot(channel, data_url=None, rect=None, viewport=dict(VIEWPORT), count=1)

    result = ElementVerifier(timeout=3.0).verify("#kw", want_shot=True)
    assert result["count"] == 1
    assert "dataUrl" not in result
    assert "error" not in result


def test_clear_preview_never_asks_for_shot(verify_env):
    """清场（``clear``）没有画面可拍：即便调用方误传 want_shot，信封也必须是 false。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_count(channel, 0)
    ElementVerifier(timeout=3.0)._exchange("", "clear", want_shot=True)
    assert channel.sent[0]["wantShot"] is False


def test_shot_payload_is_copied_not_aliased(verify_env):
    """rect/viewport 是**拷贝**回传：调用方（GUI）改动不会污染通道内部状态。"""
    _channels, set_endpoints = verify_env
    channel = FakeChannel()
    set_endpoints(["a"], {"a": channel})
    _reply_with_shot(channel, rect=dict(RECT), viewport=dict(VIEWPORT))
    result = ElementVerifier(timeout=3.0).verify("#kw", want_shot=True)
    result["rect"]["left"] = -999
    assert result["rect"]["left"] == -999  # 改的是返回值的副本（隔离成立）
    assert channel.outbox == []  # 回传早已被配对取走


# ---- preview_box_in_image：视口 CSS 像素 → 截图物理像素 ----------------------


def test_preview_box_scales_by_image_size_not_assumed_dpr():
    """换算基准是**图的实际宽高**，不是 ``dpr``：Windows 分数缩放下图宽与
    ``round(视口宽 × dpr)`` 可能差 1px，而小元素上偏 1px 就是「框没套住」。"""
    from rpa_core.capture.verify import preview_box_in_image

    box = preview_box_in_image(RECT, VIEWPORT, (2560, 1440))
    assert box == {"x": 200.0, "y": 100.0, "width": 400.0, "height": 160.0}


def test_preview_box_falls_back_to_dpr_without_image_size():
    """图尺寸还没解出来时退回 dpr——至少同量纲，比假装 100% 缩放强。"""
    from rpa_core.capture.verify import preview_box_in_image

    assert preview_box_in_image(RECT, VIEWPORT, None) == {
        "x": 200.0, "y": 100.0, "width": 400.0, "height": 160.0
    }
    # 连 viewport 都没有 ⇒ 视作 dpr=1（CSS 像素直接用），不是崩、也不是 None
    assert preview_box_in_image(RECT, None, None) == {
        "x": 100.0, "y": 50.0, "width": 200.0, "height": 80.0
    }


def test_preview_box_ignores_scroll_offset():
    """**刻意不用滚动偏移**：截图拍的就是视口，rect 也是视口坐标——同源。
    加 scrollX/Y 只会把框推到图外面去（钉住这条，免得日后「好心」加上）。"""
    from rpa_core.capture.verify import preview_box_in_image

    scrolled = preview_box_in_image(RECT, VIEWPORT, (2560, 1440))
    same = dict(VIEWPORT, scrollX=0, scrollY=0)
    assert preview_box_in_image(RECT, same, (2560, 1440)) == scrolled


def test_preview_box_returns_none_when_nothing_to_draw():
    """三种「不画框」都返回 None，绝不画假框：无rect / 零面积 / 形状不对。
    （画一个 0×0 或负坐标的框，用户会以为「框住了但选不中」而反复折腾。）"""
    from rpa_core.capture.verify import preview_box_in_image

    assert preview_box_in_image(None, VIEWPORT, (2560, 1440)) is None
    assert preview_box_in_image([1, 2], VIEWPORT, (2560, 1440)) is None
    assert preview_box_in_image(
        {"left": 1, "top": 1, "width": 0, "height": 40}, VIEWPORT, (2560, 1440)
    ) is None
    assert preview_box_in_image({"left": "a"}, VIEWPORT, (2560, 1440)) is None


def test_preview_box_clamps_partially_scrolled_out():
    """元素有一部分滚出视口时 rect.left/top 为负：**钳到 0 且同时收窄宽高**。

    M47.12 修的是真bug：原先只把 x 钉到 0 而不动 width，元素横跨 -50~30（宽 30,
    scale=2 ⇒ 宽 60）时框会从0 铺到 60——比元素可见部分宽一倍，看着像框住了旁边
    的东西。正确是「切掉的那一截从宽高里扣掉」：-50*2=-100 被切 100 ⇒ 60-100 <0
    ⇒ 说明元素**完全在视口外**，此时应返回 None。
    """
    from rpa_core.capture.verify import preview_box_in_image

    # 部分露出：left=-30, 宽 100, scale=2 ⇒ 图内 x=-60, 宽 200 ⇒ 切掉 60 ⇒ 宽 140
    assert preview_box_in_image(
        {"left": -30, "top": -10, "width": 100, "height": 30}, VIEWPORT, (2560, 1440)
    ) == {"x": 0.0, "y": 0.0, "width": 140.0, "height": 40.0}

    # 完全在视口外（左边整块都在外面）⇒ 不画框
    assert (
        preview_box_in_image(
            {"left": -500, "top": 10, "width": 30, "height": 30}, VIEWPORT, (2560, 1440)
        )
        is None
    )


def test_preview_box_survives_junk_image_size():
    """图尺寸是垃圾（解码失败/元数据缺失）时退回 dpr，不抛——预览页签不该因
    一张图崩掉整个编辑器。"""
    from rpa_core.capture.verify import preview_box_in_image

    assert preview_box_in_image(RECT, VIEWPORT, ("x", None)) == {
        "x": 200.0, "y": 100.0, "width": 400.0, "height": 160.0
    }
