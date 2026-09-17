"""S0 spike：注册/注销 native messaging host，并发现 Load unpacked 扩展 ID。

用法（Windows 默认写 HKCU，免管理员）：
    uv run python .harness/spike/native_messaging/register_host.py --browser edge
    uv run python .harness/spike/native_messaging/register_host.py --browser both
    uv run python .harness/spike/native_messaging/register_host.py --browser edge --unregister

发现顺序：先按源码目录从浏览器 profile 的 Secure Preferences 里找 location==4
（Load unpacked）的扩展 ID；找不到时可用 --extension-id 手工指定。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent
EXTENSION_DIR = SPIKE_DIR / "extension"
HOST_SCRIPT = SPIKE_DIR / "host" / "spike_host.py"
GENERATED_DIR = SPIKE_DIR / "host" / "generated"
DEFAULT_HOST_NAME = "com.rpa_core.spike"

WINDOWS_REGISTRY_KEYS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}
POSIX_DIRS = {
    "chrome": (
        Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts",
        Path.home()
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts",
    ),
    "edge": (
        Path.home() / ".config" / "microsoft-edge" / "NativeMessagingHosts",
        Path.home()
        / "Library"
        / "Application Support"
        / "Microsoft Edge"
        / "NativeMessagingHosts",
    ),
}


def _normalize(path: str) -> str:
    text = str(path or "").replace("/", "\\" if os.name == "nt" else "/")
    return os.path.normcase(os.path.normpath(text))


def _user_data_dirs(browser: str) -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return []
        relative = {
            "chrome": Path("Google") / "Chrome" / "User Data",
            "edge": Path("Microsoft") / "Edge" / "User Data",
        }[browser]
        return [Path(local) / relative]
    if sys.platform == "darwin":
        base = {
            "chrome": Path("Library") / "Application Support" / "Google" / "Chrome",
            "edge": Path("Library") / "Application Support" / "Microsoft Edge",
        }[browser]
        return [home / base]
    base = {
        "chrome": Path(".config/google-chrome"),
        "edge": Path(".config/microsoft-edge"),
    }[browser]
    return [home / base]


def discover_extension_id(browser: str, extension_dir: Path) -> tuple[str | None, list[str]]:
    """按源码目录匹配 Load unpacked 记录，返回 (扩展 ID, 命中的 profile 名列表)。"""
    target = _normalize(str(extension_dir.resolve()))
    hits: list[str] = []
    found: str | None = None
    for user_data in _user_data_dirs(browser):
        if not user_data.is_dir():
            continue
        for profile in sorted(user_data.iterdir()):
            if not profile.is_dir():
                continue
            secure = profile / "Secure Preferences"
            if not secure.is_file():
                continue
            try:
                data = json.loads(secure.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            settings = ((data.get("extensions") or {}).get("settings")) or {}
            for extension_id, record in settings.items():
                if not isinstance(record, dict):
                    continue
                if record.get("location") != 4:
                    continue
                if _normalize(record.get("path") or "") == target:
                    found = found or extension_id
                    hits.append(f"{profile.name}:{extension_id}")
    return found, hits


def host_command() -> str:
    """host manifest 的 path。

    优先用产品入口 ``rpa-core-ext-host``（无参数 .exe，避免依赖 manifest path 的
    参数解析）；缺失时退回 ``pythonw`` + spike_host 脚本（自包含，需带参数）。
    """
    python = Path(sys.executable)
    scripts = python.parent
    entry = scripts / ("rpa-core-ext-host.exe" if os.name == "nt" else "rpa-core-ext-host")
    if entry.is_file():
        return str(entry)
    if sys.platform == "win32":
        pythonw = python.with_name("pythonw.exe")
        exe = pythonw if pythonw.is_file() else python
        return f'"{exe}" "{HOST_SCRIPT}"'
    launcher = GENERATED_DIR / f"spike_host_{os.getpid()}.sh"
    launcher.write_text(
        f'#!/bin/sh\nexec "{python}" "{HOST_SCRIPT}"\n', encoding="utf-8"
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return str(launcher)


def write_manifest(browser: str, host_name: str, extension_id: str) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = GENERATED_DIR / f"{host_name}.{browser}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": host_name,
                "description": "rpa_core native messaging spike host",
                "path": host_command(),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id}/"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def register(browser: str, host_name: str, manifest_path: Path) -> str:
    if sys.platform == "win32":
        import winreg

        key_path = f"{WINDOWS_REGISTRY_KEYS[browser]}\\{host_name}"
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        return f"HKCU\\{key_path}"
    for directory in POSIX_DIRS[browser]:
        if directory.parent.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{host_name}.json"
            target.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
            return str(target)
    raise SystemExit(f"no native messaging directory for {browser}")


def unregister(browser: str, host_name: str) -> str:
    if sys.platform == "win32":
        import winreg

        key_path = f"{WINDOWS_REGISTRY_KEYS[browser]}\\{host_name}"
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            return f"(absent) HKCU\\{key_path}"
        return f"deleted HKCU\\{key_path}"
    removed = []
    for directory in POSIX_DIRS[browser]:
        target = directory / f"{host_name}.json"
        if target.is_file():
            target.unlink()
            removed.append(str(target))
    return ", ".join(removed) if removed else "(absent)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=["edge", "chrome", "both"], default="edge")
    parser.add_argument("--host-name", default=DEFAULT_HOST_NAME)
    parser.add_argument("--extension-dir", type=Path, default=EXTENSION_DIR)
    parser.add_argument("--extension-id", default="")
    parser.add_argument("--unregister", action="store_true")
    args = parser.parse_args()

    browsers = ["edge", "chrome"] if args.browser == "both" else [args.browser]
    if args.unregister:
        for browser in browsers:
            print(f"{browser}: {unregister(browser, args.host_name)}")
        return 0

    extension_id = args.extension_id.strip()
    if not extension_id:
        for browser in browsers:
            found, hits = discover_extension_id(browser, args.extension_dir)
            print(f"{browser}: discovered={found or '(none)'} hits={hits or '[]'}")
            if found:
                extension_id = found
                break
    if not extension_id:
        print(
            "未发现扩展 ID。请先在浏览器里 Load unpacked 加载：\n"
            f"  {args.extension_dir}\n"
            "然后重跑本脚本；或用 --extension-id <ID> 手工指定。",
            file=sys.stderr,
        )
        return 2

    for browser in browsers:
        manifest_path = write_manifest(browser, args.host_name, extension_id)
        location = register(browser, args.host_name, manifest_path)
        print(f"{browser}: manifest={manifest_path}")
        print(f"{browser}: registered={location}")

    print(f"extensionId={extension_id}")
    print(f"hostName={args.host_name}")
    print(
        "下一步：到 edge://extensions 点该扩展的「重新加载」（或等 ≤30s 的 alarm 兜底），"
        "然后看 host 日志（默认 %TEMP%\\rpa_nm_spike_host.log）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
