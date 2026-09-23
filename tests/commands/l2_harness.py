"""L2 真机冒烟的共享装配（pytest 驱动与 spike 探针共用一份）。

为什么抽出来：探针（`.harness/spike/`）要在**写期望之前**拿真机实测值，它跑的是
与驱动完全相同的装配——起靶页服务、拉起隔离浏览器、等扩展上线、精确收尾。
两处各写一份就是 S1.2「同一件事两处口径」的假绿灯温床。

用法（`with` 一块地进出）::

    with l2_browser_session() as l2:
        client = l2.client            # 真 ExtensionExecClient（测试前缀端点）
        url = l2.page_url("basic")    # 靶页 URL
    # 退出时：杀本测试拉起的浏览器（按 profile 路径精确匹配）、停服务、还原环境

隔离与门禁语义见 `test_browser_l2_matrix.py` docstring——这里只管装配，不管跳过
（调用方自己决定没开 `RPA_BROWSER_L2=1` 时要不要跑）。
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from rpa_core import extension_launch
from rpa_core.extension_exec import ExtensionExecClient, list_extension_endpoints

# 端点隔离前缀：host 继承浏览器环境、调用方进程读自己的 env，两边只见测试端点。
TEST_ENDPOINT_PREFIX = "rpa_core_ext_l2_"

# 扩展上线等待：冷启动 MV3 service worker + connectNative + host 拉起，给足余量。
ONLINE_TIMEOUT_SECONDS = 60.0

TESTAPPS_BROWSER = Path(__file__).resolve().parents[2] / "testapps" / "browser"

_EXE_NAMES = {"msedge": "msedge.exe", "chrome": "chrome.exe"}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静默版请求处理：access log 全吞（它只是给靶页跑腿的服务端，出问题会在
    客户端以更可诊断的方式红出来——getText 读不到值之类）。"""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class _QuietServer(http.server.ThreadingHTTPServer):
    """吞掉连接异常（浏览器 keep-alive 连接在 shutdown 时被重置是常态噪声）。"""

    def handle_error(self, request: object, client_address: object) -> None:
        pass


def kill_browser_by_profile(browser: str, profile_dir: Path) -> None:
    """按「命令行含本测试 profile 路径」精确终止浏览器进程。

    不碰同名浏览器的其它实例（维护者桌面上的真浏览器没有这段命令行）。
    用 CIM 查询而非 taskkill /T：launch_browser 是 DETACHED fire-and-forget，
    手里没有进程句柄，且浏览器是多进程树。
    """
    exe_name = _EXE_NAMES.get(browser)
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


@contextmanager
def l2_browser_session(browser: str = "msedge") -> Iterator[SimpleNamespace]:
    """起靶页服务 → 拉起隔离浏览器 → 等扩展上线；退出时精确收尾。

    产出命名空间：

    - ``client``：真 ``ExtensionExecClient``（只列测试前缀端点）；
    - ``base_url`` / ``page_url(name)``：靶页地址（``basic`` → ``basic.html``）；
    - ``browser`` / ``profile_dir`` / ``bridge_log``：诊断用。
    """
    profile_dir = Path(tempfile.mkdtemp(prefix="rpa-l2-profile-"))
    log_path = profile_dir / "ext-host.log"

    # 环境先于拉起浏览器设置：host 进程经浏览器继承这组变量。
    old_prefix = os.environ.get("RPA_EXT_ENDPOINT_PREFIX")
    old_bridge_log = os.environ.get("RPA_EXT_BRIDGE_LOG")
    os.environ["RPA_EXT_ENDPOINT_PREFIX"] = TEST_ENDPOINT_PREFIX
    os.environ["RPA_EXT_BRIDGE_LOG"] = str(log_path)

    # 靶页 HTTP 服务（线程内 serve，端口 0 = 系统分配；静默版见上）。
    handler = functools.partial(_QuietHandler, directory=str(TESTAPPS_BROWSER))
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    extension_dir = extension_launch.find_extension_dir()
    if extension_dir is None:
        server.shutdown()
        server.server_close()
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise AssertionError("找不到自研扩展目录（RPA_EXTENSION_DIR 可显式指定）")

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
            browser,
            f"{base_url}/basic.html",
            extension_dir=extension_dir,
            argv_extra=[
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                # 钉住窗口尺寸：scroll 类断言的 scrollY 精确值依赖视口高，
                # 不钉就随显示器/DPI 漂移（L2 是本机可选冒烟，接受机器相关的钉值）
                "--window-size=1280,900",
            ],
        )

        client = ExtensionExecClient()
        deadline = time.monotonic() + ONLINE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if client.status().get("online"):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"扩展 {ONLINE_TIMEOUT_SECONDS:.0f}s 内未上线。当前测试前缀端点："
                f"{list_extension_endpoints()}；bridge 日志：{log_path}"
                "（存在与否与末尾内容是第一排查点）"
            )

        yield SimpleNamespace(
            client=client,
            base_url=base_url,
            page_url=lambda name: f"{base_url}/{name}.html",
            browser=browser,
            profile_dir=profile_dir,
            bridge_log=log_path,
        )
    finally:
        server.shutdown()
        server.server_close()
        kill_browser_by_profile(browser, profile_dir)
        _restore_env()
        shutil.rmtree(profile_dir, ignore_errors=True)


__all__ = [
    "ONLINE_TIMEOUT_SECONDS",
    "TESTAPPS_BROWSER",
    "TEST_ENDPOINT_PREFIX",
    "kill_browser_by_profile",
    "l2_browser_session",
]
