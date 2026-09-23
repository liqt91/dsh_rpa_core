"""L2 真机冒烟驱动（浏览器通道）：真扩展 + 真浏览器 + 本地 HTTP 靶页。

与 L1 共用插桩形状，唯一差别是后端：`ScriptedExtension` 换成**真的**
`ExtensionExecClient`——执行器、会话解析、错误整形全部走真实实现，命令经
「扩展 connectNative → bridge host → 命名管道端点」落到真实 DOM 上
（链路见 `rpa_core/extension_exec.py` docstring）。

## 隔离（M36 杀 21 个真进程那类教训的防线）

- **端点前缀**：`RPA_EXT_ENDPOINT_PREFIX=rpa_core_ext_l2_`。host 进程继承浏览器
  环境、pytest 客户端读自己的 env，两边只见测试端点——**绝不会**把命令下到
  维护者桌面上真实浏览器的端点。
- **独立 profile**：`--user-data-dir=<临时目录>` 拉起全新浏览器实例，扩展经
  `--load-extension` 注入（unpacked ID 由路径派生，host manifest 已白名单，
  见 `extension_installer.native_host_origins`）。收尾按「命令行含该 profile
  路径」精确杀进程，不碰同名浏览器的其它实例。

## 门禁

`RPA_BROWSER_L2=1` 才跑（策略 §4 定案：浏览器侧与 `--with-desktop-e2e` 同类
显式开关）。收集层不受 `RPA_COMMAND_MATRIX` 影响——只开 L2 开关时 conftest
会把 L1 驱动全部 ignore 掉。缺省时本模块整体 skip。

## 现状（S4.1 最小链路）

只有一条冒烟：navigate 建会话 → getText 读 `#t` → 断言真实 DOM 值。
这条证明的是「起服务 + 拉起浏览器 + 真通道往返 + 会话建立」全链路；
逐命令的 `l2` 表驱动块在后续切片补。
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rpa_core import extension_launch
from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.executors.browser_ext import ExtensionExecSession
from rpa_core.extension_exec import ExtensionExecClient, list_extension_endpoints
from rpa_core.model.command import CommandInvocation

L2_ENABLED = os.environ.get("RPA_BROWSER_L2") == "1"
L2_ENV_HINT = "RPA_BROWSER_L2=1"

# 端点隔离前缀：见模块 docstring「隔离」节。host 与客户端两边同读这个环境变量。
TEST_ENDPOINT_PREFIX = "rpa_core_ext_l2_"

# 目标浏览器（msedge/chrome），默认 msedge——开发基线浏览器。
L2_BROWSER = os.environ.get("RPA_BROWSER_L2_BROWSER", "msedge").strip().lower() or "msedge"

# 扩展上线等待：冷启动 MV3 service worker + connectNative + host 拉起，给足余量。
_ONLINE_TIMEOUT_SECONDS = 60.0

TESTAPPS_BROWSER = Path(__file__).resolve().parents[2] / "testapps" / "browser"

pytestmark = pytest.mark.skipif(
    not L2_ENABLED,
    reason=f"L2 真机冒烟缺省跳过；设 {L2_ENV_HINT} 启用（会拉起真实浏览器窗口）",
)


def _run_command(
    executor: PlaywrightExecutor,
    command: str,
    inputs: dict[str, Any],
):
    """同步执行一条命令（与 L1 harness.run_command 同形，但执行器由调用方持有——

    L2 的会话是 navigate 真建的，执行器必须跨命令存活）。
    """
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="l2",
        step_id=command,
        inputs=dict(inputs),
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


def _kill_browser_by_profile(browser: str, profile_dir: Path) -> None:
    """按「命令行含本测试 profile 路径」精确终止浏览器进程。

    不碰同名浏览器的其它实例（维护者桌面上的真浏览器没有这段命令行）。
    用 CIM 查询而非 taskkill /T：launch_browser 是 DETACHED fire-and-forget，
    手里没有进程句柄，且浏览器是多进程树。
    """
    exe_name = {"msedge": "msedge.exe", "chrome": "chrome.exe"}.get(browser)
    if not exe_name:
        return
    marker = str(profile_dir)
    script = (
        f"Get-CimInstance Win32_Process -Filter \"Name='{exe_name}'\" "
        f"| Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{marker}') }} "
        f"| ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=60,
    )


@pytest.fixture(scope="session")
def l2_browser():
    """session 级装配：起靶页服务 → 拉起隔离浏览器 → 等扩展上线。

    刻意**直接调 `extension_launch.launch_browser`**：conftest 的 autouse 守卫
    `_block_destructive_browser_ops` 桩掉的是 `executors.browser` 命名空间里的
    绑定（L1 用例不许拉起浏览器），L2 的存在意义就是拉真浏览器——这是显式绕过，
    不是守卫失效。
    """
    if not L2_ENABLED:
        pytest.skip(f"L2 未启用（{L2_ENV_HINT}）")

    profile_dir = Path(tempfile.mkdtemp(prefix="rpa-l2-profile-"))
    log_path = profile_dir / "ext-host.log"

    # 环境先于拉起浏览器设置：host 进程经浏览器继承这组变量。
    old_prefix = os.environ.get("RPA_EXT_ENDPOINT_PREFIX")
    old_bridge_log = os.environ.get("RPA_EXT_BRIDGE_LOG")
    os.environ["RPA_EXT_ENDPOINT_PREFIX"] = TEST_ENDPOINT_PREFIX
    os.environ["RPA_EXT_BRIDGE_LOG"] = str(log_path)

    # 靶页 HTTP 服务（线程内 serve，端口 0 = 系统分配）。
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(TESTAPPS_BROWSER)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    extension_dir = extension_launch.find_extension_dir()
    assert extension_dir is not None, "找不到自研扩展目录（RPA_EXTENSION_DIR 可显式指定）"

    def _restore_env() -> None:
        if old_prefix is None:
            os.environ.pop("RPA_EXT_ENDPOINT_PREFIX", None)
        else:
            os.environ["RPA_EXT_ENDPOINT_PREFIX"] = old_prefix
        if old_bridge_log is None:
            os.environ.pop("RPA_EXT_BRIDGE_LOG", None)
        else:
            os.environ["RPA_EXT_BRIDGE_LOG"] = old_bridge_log

    try:
        extension_launch.launch_browser(
            L2_BROWSER,
            f"{base_url}/basic.html",
            extension_dir=extension_dir,
            argv_extra=[
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        client = ExtensionExecClient()
        deadline = time.monotonic() + _ONLINE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if client.status().get("online"):
                break
            time.sleep(0.5)
        else:
            endpoints = list_extension_endpoints()
            raise AssertionError(
                f"扩展 {_ONLINE_TIMEOUT_SECONDS:.0f}s 内未上线。"
                f"当前测试前缀端点：{endpoints}；bridge 日志：{log_path}"
                f"（存在与否与末尾内容是第一排查点）"
            )

        yield SimpleNamespace(
            client=client,
            base_url=base_url,
            browser=L2_BROWSER,
            profile_dir=profile_dir,
            bridge_log=log_path,
        )
    finally:
        server.shutdown()
        server.server_close()
        _kill_browser_by_profile(L2_BROWSER, profile_dir)
        _restore_env()
        shutil.rmtree(profile_dir, ignore_errors=True)


def test_l2_smoke_navigate_then_get_text_reads_real_dom(l2_browser):
    """最小链路：navigate 建会话 → getText 读 #t → 断言真实 DOM 值。

    隔离断言顺带做掉：客户端能看到的端点必须全部带测试前缀
    （若混进无前缀端点，说明前缀隔离没生效，后续命令可能下到真浏览器）。
    """
    endpoints = list_extension_endpoints()
    assert endpoints, "没有任何测试前缀端点（fixture 的上线等待应已挡住这种情况）"
    assert all(name.startswith(TEST_ENDPOINT_PREFIX) for name in endpoints), (
        f"端点 {endpoints} 不含测试前缀 {TEST_ENDPOINT_PREFIX!r}——隔离失效"
    )

    executor = PlaywrightExecutor(
        ext_session=ExtensionExecSession(client=l2_browser.client)
    )

    navigated = _run_command(
        executor,
        "browser.navigate",
        {
            "browserType": l2_browser.browser,
            "action": "goto",
            "url": f"{l2_browser.base_url}/basic.html",
        },
    )
    assert navigated.status == "success", navigated.model_dump_json()
    session_id = navigated.outputs["sessionId"]

    read = _run_command(
        executor,
        "browser.getText",
        {"sessionId": session_id, "selector": "#t", "infoType": "text"},
    )
    assert read.status == "success", read.model_dump_json()
    assert read.outputs["value"] == "text", (
        f"真机读到的 #t 不是 'text'：{read.outputs['value']!r}"
    )
    assert [effect.kind.value for effect in read.effects] == ["read"]
