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
from collections.abc import Sequence
from pathlib import Path

# 浏览器类型（manifest browserType 的取值）→ 候选可执行文件名
_EXE_NAMES: dict[str, tuple[str, ...]] = {
    "msedge": ("msedge.exe", "microsoft-edge", "MicrosoftEdge.exe"),
    "chrome": ("chrome.exe", "google-chrome", "chromium", "chromium-browser"),
}

# Windows 常见安装路径（浏览器类型 → 候选绝对路径模板）
_WIN_PATHS: dict[str, tuple[str, str, str, str]] = {
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

# 环境变量可显式指定可执行文件（便于测试 / 非标准安装）
_ENV_KEY: dict[str, str] = {"msedge": "RPA_MSEDGE_BIN", "chrome": "RPA_CHROME_BIN"}


class BrowserLaunchError(RuntimeError):
    """浏览器自启相关错误（找不到可执行文件 / 拉起失败）。"""


def _expand_env(path: str) -> Path:
    return Path(os.path.expandvars(path))


def find_browser_exe(browser: str) -> Path | None:
    """定位指定浏览器（msedge/chrome）的可执行文件；找不到返回 None。"""
    env_key = _ENV_KEY.get(browser)
    if env_key and os.environ.get(env_key):
        exe = _expand_env(os.environ[env_key])
        if exe.is_file():
            return exe
    # Windows：先探测已知安装路径，再兜底 PATH
    if os.name == "nt":
        for template in _WIN_PATHS.get(browser, ()):
            exe = _expand_env(template)
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
    url: str,
    *,
    extension_dir: Path | None = None,
    argv_extra: Sequence[str] = (),
) -> Path:
    """以命令行拉起指定浏览器打开 url（fire-and-forget）。

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
    argv = [str(exe), "--new-window", url]
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