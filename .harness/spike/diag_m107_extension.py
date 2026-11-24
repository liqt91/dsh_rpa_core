"""M107 诊断：为什么 L2 靶场的扩展 60s 内不上线。

只做一次「起靶页 + 拉起浏览器 + 等扩展」，**保留 profile 目录**不清理，
把 ext-host.log、浏览器进程命令行、profile 内容打出来。
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from rpa_core import extension_launch  # noqa: E402
from rpa_core.extension_exec import ExtensionExecClient, list_extension_endpoints  # noqa: E402
from tests.commands.l2_harness import (  # noqa: E402
    TEST_ENDPOINT_PREFIX,
    TESTAPPS_BROWSER,
    _QuietHandler,
    _QuietServer,
)


def main() -> int:
    profile_dir = Path(tempfile.mkdtemp(prefix="rpa-l2-diag-"))
    log_path = profile_dir / "ext-host.log"
    os.environ["RPA_EXT_ENDPOINT_PREFIX"] = TEST_ENDPOINT_PREFIX
    os.environ["RPA_EXT_BRIDGE_LOG"] = str(log_path)

    handler = functools.partial(_QuietHandler, directory=str(TESTAPPS_BROWSER))
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    ext_dir = extension_launch.find_extension_dir()
    fake_profile = "--user-data-dir=" + str(profile_dir)
    print(json.dumps({
        "profileDir": str(profile_dir),
        "logPath": str(log_path),
        "extensionDir": str(ext_dir),
        "url": f"{base_url}/basic.html",
    }, ensure_ascii=False, indent=2))

    exe = extension_launch.launch_browser(
        "msedge",
        f"{base_url}/basic.html",
        extension_dir=ext_dir,
        argv_extra=[fake_profile, "--no-first-run", "--no-default-browser-check"],
    )
    print("launched exe:", exe)

    client = ExtensionExecClient()
    deadline = time.time() + 40
    online_at = None
    while time.time() < deadline:
        if client.status().get("online"):
            online_at = time.time()
            break
        time.sleep(1)
    print("online:", bool(online_at))
    print("endpoints:", list_extension_endpoints())

    time.sleep(1)
    print("--- profileDir files (top) ---")
    for p in sorted(profile_dir.iterdir())[:20]:
        print("  ", p.name)
    print("--- ext-host.log ---")
    if log_path.is_file():
        print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
    else:
        print("(no log file)")

    print("--- browser processes with this profile ---")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" "
        f"| Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{profile_dir}') }} "
        "| Select-Object ProcessId,CommandLine | Format-List"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    print(out.stdout[:4000] or "(none)")

    print("KEEP_PROFILE=", profile_dir)
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
