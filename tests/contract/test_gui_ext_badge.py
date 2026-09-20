"""状态栏「插件通道」徽标的离线诊断文案测试。

维护者需求（2026-09-20）：徽标不能只显示「离线」——要直接说清是 bridge 没注册、
插件没装、浏览器没开，还是**浏览器开着但插件没被注入**（那条是当天实测的真因：
浏览器单实例会吞掉后续命令行的 --load-extension，探测只会笼统报离线）。

这里用假 self（一个真 QLabel）直接驱动 `_on_ext_badge`，不真起探测线程——
徽标逻辑与探测解耦，测文案不必等 5s 轮询。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

from PySide6.QtWidgets import QLabel  # noqa: E402

from rpa_core.extension_installer import (  # noqa: E402
    OFFLINE_BRIDGE_NOT_REGISTERED,
    OFFLINE_BROWSER_NOT_RUNNING,
    OFFLINE_RUNNING_WITHOUT_EXTENSION,
)
from rpa_core.gui.app import MainWindow, _ext_badge_tooltip, build_application  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return build_application()


@pytest.fixture()
def badge(qapp):
    return QLabel("")


def _diag(
    reason: str, *, running: bool, injected, bridge: bool, installed: bool,
    loaded="auto",
) -> dict:
    from rpa_core.extension_installer import (
        browser_diagnostics_line,
        describe_offline_reason,
    )

    # loaded="auto" 时跟随 injected（没在跑就一定是 False）
    resolved_loaded = injected if loaded == "auto" else loaded
    info = {
        "binary": True,
        "running": running,
        "bridgeRegistered": bridge,
        "bridgeHostExecutableExists": True,
        "bridgeExtensionId": "abcdefghijklmnop",
        "extensionInstalled": installed,
        "extensionEnabled": installed,
        "extensionUnpacked": False,
        "uninstallBlocked": False,
        "extensionInjected": injected,
        "extensionLoaded": resolved_loaded if running else False,
    }
    browsers = {"edge": info}
    return {
        "browsers": browsers,
        "reason": reason,
        "summary": describe_offline_reason(reason, browsers),
        "line": browser_diagnostics_line("edge", info),
    }


def test_offline_badge_names_bridge_and_plugin_state(badge):
    """离线徽标先答维护者的两问：bridge 注册了吗、插件装了吗。"""
    diag = _diag(
        OFFLINE_BROWSER_NOT_RUNNING, running=False, injected=False,
        bridge=True, installed=True,
    )
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), False, [], diag)
    text = badge.text()
    assert text.startswith("插件通道：离线")
    assert "bridge 已注册" in text and "插件已安装" in text and "浏览器未运行" in text
    assert badge.toolTip()  # 悬浮给明细 + 建议
    assert "建议：" in badge.toolTip()


def test_offline_badge_calls_out_running_without_extension(badge):
    """当天真因必须一眼可辨，而不是和「浏览器没开」混成同一句离线。"""
    diag = _diag(
        OFFLINE_RUNNING_WITHOUT_EXTENSION, running=True, injected=False,
        bridge=True, installed=True,
    )
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), False, [], diag)
    assert "浏览器未加载插件" in badge.text()
    assert "完全退出浏览器" in badge.toolTip()


def test_offline_badge_says_when_bridge_missing(badge):
    diag = _diag(
        OFFLINE_BRIDGE_NOT_REGISTERED, running=False, injected=False,
        bridge=False, installed=False,
    )
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), False, [], diag)
    assert "bridge 未注册" in badge.text()
    assert "先在「插件」里注册 bridge" in badge.toolTip()


def test_online_badge_ignores_diagnostics(badge):
    """在线=绿色且不提诊断（诊断只在离线时才算）。"""
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), True, ["edge"], None)
    assert badge.text() == "插件通道：在线（edge）"
    assert badge.toolTip() == ""
    assert "#1a7f37" in badge.styleSheet()


def test_badge_falls_back_when_diagnostics_unavailable(badge):
    """诊断不可用（探测失败/未安装）时退回原文案，不崩、不留空白。"""
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), False, [], None)
    assert badge.text() == "插件通道：离线（浏览器指令不可用，详见「插件」）"
    MainWindow._on_ext_badge(SimpleNamespace(_ext_badge=badge), False, [], {})
    assert badge.text() == "插件通道：离线（浏览器指令不可用，详见「插件」）"


def test_badge_tooltip_lists_each_browser(badge):
    """多浏览器时 tooltip 逐行给出各自状态（不把 chrome 的问题算到 edge 头上）。"""
    diag = _diag(
        OFFLINE_RUNNING_WITHOUT_EXTENSION, running=True, injected=False,
        bridge=True, installed=True,
    )
    diag["browsers"]["chrome"] = {
        "binary": True, "running": False, "bridgeRegistered": False,
        "bridgeHostExecutableExists": False, "bridgeExtensionId": None,
        "extensionInstalled": False, "extensionEnabled": False,
        "extensionUnpacked": False, "uninstallBlocked": False,
        "extensionInjected": False, "extensionLoaded": False,
    }
    tip = _ext_badge_tooltip(diag)
    assert "edge：" in tip and "chrome：" in tip
    assert "bridge 未注册" in tip
