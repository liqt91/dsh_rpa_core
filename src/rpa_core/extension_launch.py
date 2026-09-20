"""浏览器自启（open-webpage 的前置能力）。

扩展通道运行在「用户已启动的浏览器」里。若浏览器根本没启动，navigate goto 无法入队
命令。本模块负责：定位浏览器可执行文件 → 用命令行拉起（可选注入自研扩展 + 透传用户的
命令行参数）→ 供执行器轮询目标浏览器回到在线。保持无副作用、可被测试桩替换。

设计约束：
- 默认复用用户默认 user-data-dir（命令行为 `--new-window url --load-extension=...`）。
  若需独立配置/可靠注入，用户在 commandLineArgs 传 `--user-data-dir=<目录>` 覆盖。
- 自启只在「目标浏览器在线探测失败」时触发，因此没有正在运行的既有实例抢 --load-extension。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

# 浏览器类型（manifest browserType 的取值）→ 候选可执行文件名（PATH 兜底探测用）
_EXE_NAMES: dict[str, tuple[str, ...]] = {
    "msedge": ("msedge.exe", "microsoft-edge", "MicrosoftEdge.exe"),
    "chrome": ("chrome.exe", "google-chrome", "chromium", "chromium-browser"),
}

# Windows 常见安装路径（浏览器类型 → 候选绝对路径模板）。
# 这是「浏览器是否安装」的唯一候选来源：extension_installer 会复用本表，
# 避免两份路径表（旧 installer._browser_binary_candidates）漂移。
_WIN_INSTALL_PATHS: dict[str, tuple[str, ...]] = {
    # Edge 的 x86/标准 ProgramFiles；Chrome 的 x86/标准 ProgramFiles 和 LocalAppData 路径
    "msedge": (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ),
    "chrome": (
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ),
}

# macOS：浏览器是 .app bundle，可执行文件在 Contents/MacOS/ 下，**不在 PATH 里**，
# 也没有 `microsoft-edge` 这类命令名——只有这张表能定位到。
_MAC_INSTALL_PATHS: dict[str, tuple[str, ...]] = {
    "msedge": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
    "chrome": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ),
}

# Linux：由 PATH / shutil.which 兜底（发行版安装位置五花八门，不维护绝对路径表）
_LINUX_INSTALL_PATHS: dict[str, tuple[str, ...]] = {}

# 环境变量可显式指定可执行文件（便于测试 / 非标准安装）
_ENV_KEY: dict[str, str] = {"msedge": "RPA_MSEDGE_BIN", "chrome": "RPA_CHROME_BIN"}


class BrowserLaunchError(RuntimeError):
    """浏览器自启相关错误（找不到可执行文件 / 拉起失败）。"""


def _expand_env(path: str) -> Path:
    # expandvars 在 POSIX 上不展开 `%VAR%`（那是 Windows 语法），expanduser 负责 `~`
    return Path(os.path.expanduser(os.path.expandvars(path)))


def browser_binary_candidates(browser: str) -> list[Path]:
    """返回 browser（msedge/chrome，或 edge/chrome 别名）已知安装路径的候选。

    只列路径、不校验存在性；可供 find_browser_exe 与 extension_installer 共用，
    保证「检测安装」与「自启定位」看到同一份**当前平台**的路径表。
    """
    key = {"edge": "msedge"}.get(browser, browser)
    if sys.platform == "win32":
        templates = _WIN_INSTALL_PATHS.get(key, ())
    elif sys.platform == "darwin":
        templates = _MAC_INSTALL_PATHS.get(key, ())
    else:
        templates = _LINUX_INSTALL_PATHS.get(key, ())
    return [_expand_env(p) for p in templates]


def find_browser_exe(browser: str) -> Path | None:
    """定位指定浏览器（msedge/chrome）的可执行文件；找不到返回 None。"""
    env_key = _ENV_KEY.get(browser)
    if env_key and os.environ.get(env_key):
        exe = _expand_env(os.environ[env_key])
        if exe.is_file():
            return exe
    # 先探测当前平台的已知安装路径，再兜底 PATH
    for exe in browser_binary_candidates(browser):
        if exe.is_file():
            return exe
    # PATH 探测（跨平台兜底）
    for name in _EXE_NAMES[browser]:
        exe = shutil.which(name)
        if exe:
            return Path(exe)
    return None


def find_extension_dir() -> Path | None:
    """定位自研扩展目录（含 background.js 与 manifest.json）；找不到返回 None。

    优先读环境变量 RPA_EXTENSION_DIR，其次沿包所在目录向上查找 `extension/`。
    不在默认布局部署（site-packages 安装）时，请用环境变量显式指定。
    """
    if os.environ.get("RPA_EXTENSION_DIR"):
        d = Path(os.environ["RPA_EXTENSION_DIR"])
        if (d / "background.js").is_file():
            return d
        return None
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        ext = parent / "extension"
        if (ext / "background.js").is_file() and (ext / "manifest.json").is_file():
            return ext
    return None


def launch_browser(
    browser: str,
    url: str | None = None,
    *,
    extension_dir: Path | None = None,
    argv_extra: Sequence[str] = (),
) -> Path:
    """以命令行拉起指定浏览器（fire-and-forget）。

    传 url 时以 `--new-window <url>` 直达目标页；不传（None）则裸拉起——
    冷启动由浏览器自己打开默认启动页，已在跑时仅激活现有进程不新开页面。
    返回可执行文件路径；找不到 exe 或启动失败抛 BrowserLaunchError。
    扩展注入与自定义命令行参数均为可选。
    """
    exe = find_browser_exe(browser)
    if exe is None:
        raise BrowserLaunchError(
            f"找不到 {browser} 浏览器可执行文件，无法自动拉起。"
            "请先手动打开浏览器让自研插件上线，或用环境变量 "
            f"{_ENV_KEY[browser]} 指定可执行文件路径。"
        )
    argv = [str(exe)]
    if url:
        argv += ["--new-window", url]
    if extension_dir is not None and extension_dir.is_dir():
        argv.append(f"--load-extension={extension_dir.resolve()}")
    argv.extend([str(a) for a in argv_extra])
    try:
        creation = 0
        if os.name == "nt":
            # Windows 下抑制控制台窗口；不阻塞等待（拉起后由执行器轮询上线）
            creation = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
        )
    except OSError as exc:  # noqa: BLE001 - 启动路径不存在/无权限统一按拉起失败处理
        raise BrowserLaunchError(f"启动浏览器失败（{browser}）：{exc}") from None
    return exe