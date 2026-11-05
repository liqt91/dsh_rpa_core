#!/usr/bin/env python3
"""rpa-core 的入口垫片：把「命令在哪」从文档里挪进代码。

起因是实际踩到的坑：技能通过 npm / clone 分发，包里不带 Python 运行时，
而文档里写的是裸命令 `rpa-core catalog --view agent`。在只装了技能、rpa_core
没有进 PATH 的机器上，这一行必然 `command not found` —— 而报错发生在「用户
已经开始要流程了」的时候，看上去像技能坏了，其实只是运行时没连上。

所以技能文档里的命令一律写成：

    <python> <技能目录>/scripts/rc.py catalog --view agent

本脚本负责把「<python> 是谁、rpa_core 源码在哪」实测出来并转交，顺序是
runtime.json（安装器写的）→ 源码树 → 当前解释器已装 → PATH 上的 rpa-core。

找不到时**不猜、不静默失败**：打印一条可以直接照抄的修法，exit 2。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import _runtime as runtime

# 约定的内部退出码：子进程 import 失败（缺包、源码路径不对），可以换下一个候选。
EXIT_IMPORT_FAILED = 87
IMPORT_MARKER = "RC_IMPORT_FAILED"

# 子进程里跑的引导代码。用 -c 而不是写临时文件：少一个清理责任，
# 且 src 通过 argv 传，不用拼字符串（路径里有空格也不会破）。
BOOTSTRAP = (
    "import sys\n"
    "src = sys.argv[1]\n"
    "if src != '-':\n"
    "    sys.path.insert(0, src)\n"
    "try:\n"
    "    from rpa_core.cli import main\n"
    "except Exception as exc:\n"
    f"    sys.stderr.write('{IMPORT_MARKER} %s: %s\\n' % (type(exc).__name__, exc))\n"
    f"    sys.exit({EXIT_IMPORT_FAILED})\n"
    "sys.argv = ['rpa-core'] + sys.argv[2:]\n"
    "sys.exit(main())\n"
)

ENV_EXTRA = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _candidates() -> list[dict[str, Any]]:
    """按可信度排序的候选。每一项都能独立运行，前一个坏了就往后试。"""
    out: list[dict[str, Any]] = []
    data = runtime.load()
    recorded = data.get("rpa_core") or {}
    recorded_python = data.get("python")
    mode = recorded.get("mode")

    if mode and recorded_python and Path(recorded_python).is_file():
        if mode == "exe":
            exe = recorded.get("exe")
            if exe and Path(exe).is_file():
                out.append({"by": "runtime.json", "mode": "exe", "exe": exe})
        elif mode in ("src", "module"):
            out.append(
                {
                    "by": "runtime.json",
                    "mode": mode,
                    "python": recorded_python,
                    "src": recorded.get("src"),
                }
            )

    # 开发态 / runtime.json 丢了：向上找源码树，用当前解释器。
    root = runtime.find_source_root()
    if root:
        out.append(
            {"by": "source-tree", "mode": "src", "python": sys.executable,
             "src": str(root / "src")}
        )

    # pip 装过 rpa_core，只是没记进 runtime.json。
    out.append({"by": "current-python", "mode": "module", "python": sys.executable,
                "src": None})

    exe = shutil.which("rpa-core")
    if exe:
        out.append({"by": "PATH", "mode": "exe", "exe": exe})
    return out


def _run(candidate: dict[str, Any], argv: list[str]) -> tuple[int | None, str]:
    """跑一个候选。返回 (退出码, stderr)；退出码 None 表示「这个候选不成立，换下一个」。"""
    env = {**os.environ, **ENV_EXTRA}
    try:
        if candidate["mode"] == "exe":
            proc = subprocess.run([candidate["exe"], *argv], env=env, check=False)
            return proc.returncode, ""

        src = candidate.get("src")
        proc = subprocess.run(
            [candidate["python"], "-c", BOOTSTRAP, str(src) if src else "-", *argv],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if proc.returncode == EXIT_IMPORT_FAILED and IMPORT_MARKER in (proc.stderr or ""):
        return None, proc.stderr.strip().splitlines()[-1]
    # 正常路径：把子进程的 stdout/stderr 原样转出去（stdout 是 JSON，不能被我们改写）。
    if proc.stdout:
        sys.stdout.write(proc.stdout)
        sys.stdout.flush()
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        sys.stderr.flush()
    return proc.returncode, ""


def _panic(tried: list[dict[str, str]]) -> int:
    snapshot = runtime.describe()
    payload = {
        "ok": False,
        "code": "RPA_CORE_NOT_FOUND",
        "error": "找不到可用的 rpa-core 运行时（技能本身没问题，缺的是它依赖的 Python 那一半）",
        "tried": tried,
        "resolved": snapshot,
        "fix": [
            "装了源码树：rpa-skill install --force --rpa-core-dir <仓库根>",
            "已 pip 装过：rpa-skill install --force --python <那个解释器>",
            "手写 <技能目录>/runtime.json："
            '{"python": "...", "source_root": "..."}',
            "自检：rpa-skill doctor",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2


def main(argv: list[str]) -> int:
    tried: list[dict[str, str]] = []
    for candidate in _candidates():
        code, detail = _run(candidate, argv)
        if code is not None:
            return code
        tried.append({"by": candidate["by"], "why": detail})
    return _panic(tried)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
