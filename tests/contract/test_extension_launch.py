"""浏览器自启 + 命令行参数（auto-launch / browserType 离线拉起）单元测试。

不依赖真实 devserver/浏览器：用桩会话 + monkeypatch 可控「在线状态」与 `launch_browser`，
验证 executor 在目标浏览器离线时按 commandLineArgs 拉起并等待上线、拉不动时的可操作报错。
"""

import asyncio
import sys
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


def test_launch_browser_bare_without_url(monkeypatch, tmp_path):
    """裸拉起（url=None）：argv 不含 --new-window/URL，仅 exe + 扩展注入 + 自定义参数。"""
    exe = tmp_path / "chrome_dummy.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("RPA_CHROME_BIN", str(exe))

    captured = {}
    mock_popen = SimpleNamespace(wait=lambda: None)

    def _popen(argv, **kwargs):
        captured["argv"] = argv
        return mock_popen

    monkeypatch.setattr("subprocess.Popen", _popen)
    from rpa_core.extension_launch import launch_browser

    launch_browser("chrome", None, argv_extra=["--incognito"])
    argv = captured["argv"]
    assert argv == [str(exe), "--incognito"]
    assert "--new-window" not in argv


def test_launch_browser_missing_exe_raises(monkeypatch):
    """找不到可执行文件 → BrowserLaunchError。"""
    monkeypatch.delenv("RPA_MSEDGE_BIN", raising=False)
    monkeypatch.delenv("RPA_CHROME_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from rpa_core.extension_launch import launch_browser

    # 三平台候选表全部清空：否则在装了 Edge/Chrome 的机器上会真的命中并拉起浏览器
    for table in ("_WIN_INSTALL_PATHS", "_MAC_INSTALL_PATHS", "_LINUX_INSTALL_PATHS"):
        monkeypatch.setattr(f"rpa_core.extension_launch.{table}", {})

    with pytest.raises(BrowserLaunchError):
        launch_browser("msedge", "https://a.test/x")


# ---- executor: 离线自启 / 拉不动报错 ---------------------------------------------------


def test_navigate_offline_nothing_running_launches_then_opens(monkeypatch):
    """无任何插件在跑 + 指定 browserType（冷启动）→ 裸拉起 → tabs.create 创建目标页。

    关键设计（维护者定案）：拉起不带 URL；浏览器冷启动自己打开的启动页**原样保留**
    （与影刀一致——关闭/导航都是「指令以外的操作」，发生在用户眼前观感差）；
    目标页由 tabs.create 创建（创建即返回 tabId，程序创建的标签页地址栏不聚焦）。
    """
    ext_calls = []
    launch_browser_calls = {}

    def _tabs_create(url, timeout_seconds=30, target_host=None):
        ext_calls.append(("tabs.create", url, target_host))
        return {"tabId": 7, "url": url, "completed": True, "timedOut": False}

    def _launch(browser, url, extension_dir=None, argv_extra=()):
        launch_browser_calls["browser"] = browser
        launch_browser_calls["url"] = url
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
    assert launch_browser_calls.get("url") is None  # 裸拉起，不带 URL
    assert launch_browser_calls.get("argv_extra") == ["--incognito"]
    # 仅一次 tabs.create，没有任何对启动页的操作
    assert ext_calls == [("tabs.create", "https://a.test/2", "msedge")]
    assert result.outputs["tabId"] == "7"


def test_navigate_online_but_target_not_claiming_launches_and_retries(monkeypatch):
    """有活跃宿主（Edge 在线）但目标 Chrome 无人领取 → 裸拉起激活 → 重试创建成功。

    对应「插件 MV3 SW 休眠」场景：浏览器进程本就在跑，裸拉起不新开页面，
    目标页由拉起后重试的 tabs.create 创建（创建即返回 tabId）。
    """
    ext_calls = []
    launches = []

    def _tabs_create(url, timeout_seconds=30, target_host=None):
        ext_calls.append(target_host)
        if target_host == "chrome" and "chrome" not in executor._ext.client.hosts:
            raise ExtensionChannelError("TIMEOUT", "chrome not claiming yet")
        return {"tabId": 7, "url": "https://a.test/2", "completed": True}

    def _launch(browser, url, extension_dir=None, argv_extra=()):
        launches.append((browser, url))
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
    assert launches == [("chrome", None)]  # 裸拉起
    assert ext_calls == ["chrome", "chrome"]  # 首次无人领取，拉起后重试创建
    assert result.outputs["tabId"] == "7"


def test_navigate_cold_start_never_touches_user_tabs(monkeypatch):
    """冷启动（无论启动页是 NTP 还是恢复上次会话）：用户已有标签页绝不被导航/关闭。

    目标页只通过 tabs.create 新增一个；启动页原样保留（维护者对照影刀定案）。
    """
    ext_calls = []

    def _tabs_create(url, timeout_seconds=30, target_host=None):
        ext_calls.append(("tabs.create", url, target_host))
        return {"tabId": 7, "url": url, "completed": True}

    def _launch(browser, url, extension_dir=None, argv_extra=()):
        executor._ext.client.hosts = ["msedge"]

    monkeypatch.setattr("rpa_core.executors.browser.launch_browser", _launch)
    monkeypatch.setattr("rpa_core.executors.browser.find_extension_dir", lambda: None)

    async def _go():
        nonlocal executor
        executor = build_executor([])
        executor._ext.tabs_create = _tabs_create
        # 若误调 tabs.navigate / tabs.close / tabs.list 即失败：
        # 不探测、不导航、不关闭任何既有标签页
        def _forbidden(*args, **kwargs):
            raise AssertionError(f"不应触碰既有标签页: {args} {kwargs}")

        executor._ext.tabs_navigate = _forbidden
        executor._ext.tabs_close = _forbidden
        executor._ext.tabs_list = _forbidden
        result = await _run(executor, _invocation("https://a.test/2", browserType="msedge"))
        return result

    executor = None
    result = asyncio.run(_go())
    assert result.status == "success", result.error
    assert ext_calls == [("tabs.create", "https://a.test/2", "msedge")]
    assert result.outputs["tabId"] == "7"


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

# ---- 平台化路径表（macOS 适配）-------------------------------------------------------


def test_binary_candidates_are_platform_specific(monkeypatch):
    """候选路径表按平台选择：win32 用 %VAR% 模板、darwin 用 .app bundle、其它走 PATH 兜底。"""
    from rpa_core import extension_launch as el

    monkeypatch.setattr(sys, "platform", "win32")
    win = {str(p) for p in el.browser_binary_candidates("msedge")}
    assert any(p.endswith("msedge.exe") for p in win)

    monkeypatch.setattr(sys, "platform", "darwin")
    # 本用例可能在 Windows 主机上模拟 darwin：Path() 会按主机规范成反斜杠，
    # 因此统一以 as_posix() 的 POSIX 形态断言（真实 mac 主机上为恒等转换）。
    mac = [p.as_posix() for p in el.browser_binary_candidates("msedge")]
    assert mac == ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    mac_chrome = [p.as_posix() for p in el.browser_binary_candidates("chrome")]
    assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in mac_chrome
    # `~` 必须展开（POSIX 上字符串字面量 `~` 会被 expandvars 原样留下）
    assert all("~" not in p for p in mac_chrome)

    monkeypatch.setattr(sys, "platform", "linux")
    assert el.browser_binary_candidates("chrome") == []


def test_windows_templates_are_not_returned_off_windows(monkeypatch):
    """回归：POSIX 上 expandvars 不展开 `%ProgramFiles%`，旧表会让检测拿到字面量假路径。"""
    from rpa_core import extension_launch as el

    monkeypatch.setattr(sys, "platform", "darwin")
    for browser in ("chrome", "msedge"):
        for path in el.browser_binary_candidates(browser):
            assert "%" not in str(path)


def test_find_browser_exe_detects_mac_bundle(monkeypatch, tmp_path):
    """darwin 下 find_browser_exe 能命中 .app bundle 里的可执行文件（无需在 PATH 里）。"""
    from rpa_core import extension_launch as el

    bundle = tmp_path / "Microsoft Edge.app" / "Contents" / "MacOS"
    bundle.mkdir(parents=True)
    exe = bundle / "Microsoft Edge"
    exe.write_bytes(b"\xcf\xfa\xed\xfe")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(el, "_MAC_INSTALL_PATHS", {"msedge": (str(exe),)})
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.delenv("RPA_MSEDGE_BIN", raising=False)

    assert el.find_browser_exe("msedge") == exe
    assert el.find_browser_exe("edge") == exe  # edge 是 msedge 的别名


def test_find_browser_exe_falls_back_to_path_on_linux(monkeypatch, tmp_path):
    """Linux 不维护绝对路径表：候选为空时仍靠 PATH 探测兜底。"""
    from rpa_core import extension_launch as el

    exe = tmp_path / "google-chrome"
    exe.write_bytes(b"\x7fELF")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: str(exe) if name == "google-chrome" else None)
    monkeypatch.delenv("RPA_CHROME_BIN", raising=False)

    assert el.find_browser_exe("chrome") == exe
