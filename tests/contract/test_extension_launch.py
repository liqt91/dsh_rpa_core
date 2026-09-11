"""浏览器自启 + 命令行参数（auto-launch / browserType 离线拉起）单元测试。

不依赖真实 devserver/浏览器：用桩会话 + monkeypatch 可控「在线状态」与 `launch_browser`，
验证 executor 在目标浏览器离线时按 commandLineArgs 拉起并等待上线、拉不动时的可操作报错。
"""

import asyncio
from types import SimpleNamespace

import pytest

from rpa_core.executors.browser import PlaywrightExecutor
from rpa_core.extension_exec import ExtensionChannelError
from rpa_core.extension_launch import BrowserLaunchError
from rpa_core.model.command import CommandInvocation

# ---- 桩：可编程的扩展会话（只暴露 executor 用到的 client.status） ----------------------


class _StubClient:
    def __init__(self, hosts):
        self.hosts = hosts

    def status(self):
        return {"online": bool(self.hosts), "hosts": list(self.hosts)}


class _StubExt:
    def __init__(self, hosts):
        self.client = _StubClient(hosts)


class _RecordingExt:
    """记录每次 op submit 的 target_host，用于验证「会话绑定浏览器→后续操作路由到同一浏览器」。"""

    def __init__(self, hosts):
        self.hosts = list(hosts)
        self.submitted: list[tuple[str, str | None]] = []  # (op, target_host)
        self.client = SimpleNamespace(
            status=lambda: {"online": True, "hosts": list(self.hosts)}
        )

    def status(self):
        return {"online": True, "hosts": self.hosts}

    def online(self):
        return True

    def host(self):
        return None

    def tabs_create(self, url, *, timeout_seconds=5, target_host=None):
        self.submitted.append(("tabs.create", target_host))
        return {"tabId": "7", "url": url, "completed": True}

    def page_call(self, tab_id, selector, method, *, args=None,
                  timeout_seconds=5, target_host=None):
        self.submitted.append((f"page.call:{method}", target_host))
        return {"matchedCount": 1, "result": None}


def _invocation(url, **extra):
    inputs = {"url": url, **extra}
    return CommandInvocation(
        command_id="browser.navigate",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )


def build_executor(hosts):
    return PlaywrightExecutor(ext_session=_StubExt(hosts))


async def _run(executor, invocation):
    try:
        return await executor.execute(invocation, asyncio.Event())
    finally:
        await executor.close()


# ---- find_browser_exe / launch 参数组装（透传样例） ------------------------------------


def test_find_browser_exe_uses_env_override(tmp_path, monkeypatch):
    """环境变量 RPA_CHROME_BIN 显式指向可执行文件时优先采用。"""
    exe = tmp_path / "chrome_dummy.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("RPA_CHROME_BIN", str(exe))
    from rpa_core.extension_launch import find_browser_exe

    assert find_browser_exe("chrome") == exe
    assert find_browser_exe("chrome").is_file()


def test_launch_browser_builds_argv_with_extension_and_args(monkeypatch, tmp_path):
    """launch_browser 组装 argv：--new-window url + --load-extension + commandLineArgs。"""
    exe = tmp_path / "edge_dummy.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("RPA_MSEDGE_BIN", str(exe))
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    (ext_dir / "background.js").write_text("x")
    (ext_dir / "manifest.json").write_text("{}")

    captured = {}
    mock_popen = SimpleNamespace(wait=lambda: None)

    def _popen(argv, **kwargs):
        captured["argv"] = argv
        return mock_popen

    monkeypatch.setattr("subprocess.Popen", _popen)
    from rpa_core.extension_launch import launch_browser

    launch_browser("msedge", "https://a.test/x", extension_dir=ext_dir,
                   argv_extra=["--user-data-dir=C:/tmp/u", "--incognito"])
    argv = captured["argv"]
    assert argv[1] == "--new-window"
    assert argv[2] == "https://a.test/x"
    assert argv[3] == f"--load-extension={ext_dir.resolve()}"
    assert argv[4] == "--user-data-dir=C:/tmp/u"
    assert argv[5] == "--incognito"


def test_launch_browser_missing_exe_raises(monkeypatch):
    """找不到可执行文件 → BrowserLaunchError。"""
    monkeypatch.delenv("RPA_MSEDGE_BIN", raising=False)
    monkeypatch.delenv("RPA_CHROME_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from rpa_core.extension_launch import launch_browser

    monkeypatch.setattr("rpa_core.extension_launch._WIN_PATHS", {})

    with pytest.raises(BrowserLaunchError):
        launch_browser("msedge", "https://a.test/x")


# ---- executor: 离线自启 / 拉不动报错 ---------------------------------------------------


def test_navigate_offline_nothing_running_launches_then_opens(monkeypatch):
    """无任何插件在跑 + 指定 browserType → 按 commandLineArgs 拉起 → 上线后创建标签页成功。"""
    ext_calls = []
    launch_browser_calls = {}

    def _tabs_create(url, timeout_seconds=30, target_host=None):
        ext_calls.append((url, target_host))
        return {"tabId": 7, "url": "https://a.test/2", "completed": True}

    def _launch(browser, url, extension_dir=None, argv_extra=()):
        launch_browser_calls["browser"] = browser
        launch_browser_calls["argv_extra"] = list(argv_extra)
        # 模拟：拉起后该浏览器扩展上报上线，轮询读到 msedge
        executor._ext.client.hosts = ["msedge"]

    monkeypatch.setattr("rpa_core.executors.browser.launch_browser", _launch)
    monkeypatch.setattr("rpa_core.executors.browser.find_extension_dir", lambda: None)

    async def _go():
        nonlocal executor
        executor = build_executor([])
        executor._ext.tabs_create = _tabs_create
        result = await _run(executor, _invocation(
            "https://a.test/2", browserType="msedge", commandLineArgs=["--incognito"],
        ))
        return result

    executor = None
    result = asyncio.run(_go())
    assert result.status == "success", result.error
    assert launch_browser_calls.get("browser") == "msedge"
    assert launch_browser_calls.get("argv_extra") == ["--incognito"]
    assert ext_calls == [("https://a.test/2", "msedge")]


def test_navigate_online_but_target_not_claiming_launches_and_retries(monkeypatch):
    """有活跃宿主（Edge 在线）但目标 Chrome 无人领取 → 命令级等待后拉起 Chrome → 重试成功。

    对应「插件 MV3 SW 休眠」场景：目标浏览器其实存在，只是当前无人领取命令——不再因
    时序竞争误判离线，而是等命令超时后才判定需要拉起。
    """
    ext_calls = []
    launches = []

    def _tabs_create(url, timeout_seconds=30, target_host=None):
        ext_calls.append(target_host)
        if target_host == "chrome" and "chrome" not in executor._ext.client.hosts:
            raise ExtensionChannelError("TIMEOUT", "chrome not claiming yet")
        return {"tabId": 7, "url": "https://a.test/2", "completed": True}

    def _launch(browser, url, extension_dir=None, argv_extra=()):
        launches.append(browser)
        executor._ext.client.hosts = ["chrome"]  # 拉起后该浏览器上报上线

    monkeypatch.setattr("rpa_core.executors.browser.launch_browser", _launch)
    monkeypatch.setattr("rpa_core.executors.browser.find_extension_dir", lambda: None)

    async def _go():
        nonlocal executor
        executor = build_executor(["msedge"])  # 仅 Edge 在线，目标 Chrome 未在领取
        executor._ext.tabs_create = _tabs_create
        result = await _run(executor, _invocation("https://a.test/2", browserType="chrome"))
        return result

    executor = None
    result = asyncio.run(_go())
    assert result.status == "success", result.error
    assert launches == ["chrome"]
    assert ext_calls == ["chrome", "chrome"]  # 首次无人领取，拉起后重试


def test_navigate_offline_launch_fails_reports_actionable(monkeypatch):
    """命令无人领取且拉起抛 BrowserLaunchError → 返回可操作错误，不白等。"""
    def _launch(browser, url, extension_dir=None, argv_extra=()):
        raise BrowserLaunchError("找不到 edge 浏览器可执行文件")

    monkeypatch.setattr("rpa_core.executors.browser.launch_browser", _launch)
    monkeypatch.setattr("rpa_core.executors.browser.find_extension_dir", lambda: None)

    async def _go():
        executor = build_executor([])
        result = await _run(executor, _invocation("https://a.test/2", browserType="msedge"))
        return result

    result = asyncio.run(_go())
    assert result.status == "error"
    assert result.error.details["reason"] == "browser_launch_failed"
    assert "browser_launch_failed" in result.error.details["reason"]


def test_navigate_online_no_launch_command_driven(monkeypatch):
    """插件在跑（命令被正常领取）→ 直接成功，不触发拉起。"""
    launched = []

    def _launch(*a, **k):
        launched.append(a)

    monkeypatch.setattr("rpa_core.executors.browser.launch_browser", _launch)
    monkeypatch.setattr("rpa_core.executors.browser.find_extension_dir", lambda: None)

    async def _go():
        executor = build_executor(["msedge"])
        executor._ext.tabs_create = lambda url, timeout_seconds=30, target_host=None: {
            "tabId": 7, "url": "https://a.test/2", "completed": True,
        }
        result = await _run(executor, _invocation("https://a.test/2", browserType="msedge"))
        return result

    result = asyncio.run(_go())
    assert result.status == "success", result.error
    assert launched == []


def test_session_routes_followup_ops_to_bound_browser():
    """多浏览器共存（Edge+Chrome 都装了插件）时：打开网页绑定 browserType 指定的浏览器，
    后续元素操作只路由到同一个浏览器，不会串台。"""
    ext = _RecordingExt(["msedge", "chrome"])
    executor = PlaywrightExecutor(ext_session=ext)

    async def _go():
        opened = await executor.execute(
            _invocation("https://a.test/x", browserType="chrome"), asyncio.Event()
        )
        session_id = opened.outputs["sessionId"]
        return await executor.execute(
            CommandInvocation(
                command_id="browser.click",
                command_version="1.0.0",
                run_id="run",
                step_id="step-2",
                inputs={"sessionId": session_id, "selector": "#btn"},
            ),
            asyncio.Event(),
        )

    click_result = asyncio.run(_go())
    assert click_result.status == "success"
    # 打开网页时 target_host=chrome；随后的点击操作也必须 target_host=chrome
    assert ext.submitted[0] == ("tabs.create", "chrome")
    assert ("page.call:click", "chrome") in ext.submitted