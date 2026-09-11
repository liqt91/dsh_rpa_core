"""扩展通道「宿主浏览器标识」解析单测（extension_exec.browser_name_from_user_agent）。

背景：扩展通道没有"启动浏览器"概念——能用的浏览器只有扩展宿主那一个，宿主标识
仅用于状态展示（channel 概念已随 playwright 一并移除）。
"""

import pytest

from rpa_core.extension_exec import browser_name_from_user_agent

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
