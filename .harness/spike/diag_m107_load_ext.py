"""M107 实验：定位 --load-extension 为什么没加载扩展。

变体：
  A 现状：--load-extension=<中文路径>
  B 加上 --disable-extensions-except=<同一路径>（Chromium 新版加载开发者扩展的要求）
  C 把扩展复制到纯 ASCII 路径 + 注册 host，再 --load-extension（排除中文路径问题）

每个变体起独立 profile，10s 后读 Default/Preferences 的 extensions.settings 计数。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rpa_core import extension_launch  # noqa: E402
from tests.commands.l2_harness import kill_browser_by_profile  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def launch(profile: Path, ext: Path, extra: list[str]) -> None:
    argv = [
        EDGE,
        "--new-window", "about:blank",
        f"--load-extension={ext}",
        *extra,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print("ARGV:", json.dumps(argv, ensure_ascii=False))
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )


def count_extensions(profile: Path) -> dict:
    pref = profile / "Default" / "Preferences"
    if not pref.is_file():
        return {"preferences": False}
    try:
        data = json.loads(pref.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {"readError": type(exc).__name__}
    settings = (data.get("extensions") or {}).get("settings") or {}
    return {
        "preferences": True,
        "count": len(settings),
        "names": [
            (v.get("manifest") or {}).get("name")
            for v in settings.values()
        ],
    }


def variant(name: str, ext: Path, extra: list[str]) -> dict:
    profile = Path(tempfile.mkdtemp(prefix=f"m107-{name}-"))
    launch(profile, ext, extra)
    time.sleep(10)
    result = {"variant": name, "profile": str(profile), "ext": str(ext), **count_extensions(profile)}
    kill_browser_by_profile("msedge", profile)
    time.sleep(1.5)
    shutil.rmtree(profile, ignore_errors=True)
    return result


def main() -> int:
    ext_cn = Path(extension_launch.find_extension_dir())
    results = []

    results.append(variant("A-plain", ext_cn, []))

    results.append(variant("B-except", ext_cn, [f"--disable-extensions-except={ext_cn}"]))

    ascii_dir = Path(r"D:\temp\m107_ext_ascii")
    shutil.rmtree(ascii_dir, ignore_errors=True)
    shutil.copytree(ext_cn, ascii_dir)
    results.append(variant("C-ascii", ascii_dir, []))
    results.append(
        variant("D-ascii-except", ascii_dir, [f"--disable-extensions-except={ascii_dir}"])
    )

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
