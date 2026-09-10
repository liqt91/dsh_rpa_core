"""扩展通道「浏览器类型」解析单测（extension_exec.browser_name_from_user_agent /
channel_matches_host）。

背景：扩展通道没有"启动浏览器"概念——能用的浏览器只有扩展宿主那一个。因此
navigate.channel 在该通道下的语义是「校验宿主」，本文件锁死这套判定规则。
"""

import pytest

from rpa_core.extension_exec import browser_name_from_user_agent, channel_matches_host

UA_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91"
)
UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
UA_BRAVE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Brave/120"
)
UA_OPERA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0"
)
UA_FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"


@pytest.mark.parametrize(
    ("ua", "expected"),
    [
        (UA_EDGE, "msedge"),
        (UA_CHROME, "chrome"),
        (UA_BRAVE, "brave"),
        (UA_OPERA, "opera"),
        (UA_FIREFOX, "firefox"),
        ("", None),
        ("curl/8.0", None),
    ],
)
def test_browser_name_from_user_agent(ua, expected):
    assert browser_name_from_user_agent(ua) == expected


@pytest.mark.parametrize(
    ("channel", "host", "expected"),
    [
        # channel 缺省 = 跟随宿主，一律通过
        ("", "msedge", True),
        ("", None, True),
        # 同名匹配
        ("msedge", "msedge", True),
        ("chrome", "chrome", True),
        # chromium = 任意 Chromium 内核发行版
        ("chromium", "chrome", True),
        ("chromium", "msedge", True),
        ("chromium", "brave", True),
        ("chromium", "firefox", False),
        # 不同名不通过（Edge 不是 Chrome，反之亦然）
        ("msedge", "chrome", False),
        ("chrome", "msedge", False),
        # 宿主未上报（旧版扩展）：无法确认，不假装成功
        ("msedge", None, False),
        ("chromium", None, False),
        # 非 Chromium 宿主不满足 chromium
        ("firefox", "firefox", True),
    ],
)
def test_channel_matches_host(channel, host, expected):
    assert channel_matches_host(channel, host) is expected
