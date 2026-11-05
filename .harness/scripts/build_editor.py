"""构建可分发的编辑窗口（PPX 应用），产出 zip。

为什么需要这个脚本，而不是直接 `ppx build`：

1. ppx 生成的 PyInstaller spec 只收三样 data —— 前端产物、`ppx.toml`、`api/resources`。
   而 `rpa_core` 的 `commands/` 目录与 `gui/i18n.js` 是**包内数据**，不会自动进包。
   不复制的话，冻结态启动后 catalog 目录为空（界面没有指令）、中文名全丢。
2. PyInstaller 要从构建环境 import `rpa_core`，而 ppx 自己的 venv 里没有 pydantic /
   jsonschema（ppx 不依赖它们）。缺了只会打 WARNING，产出一个「能启动但一点就炸」的包
   —— 这个坑已经踩过一次（ModuleNotFoundError: jsonschema）。
3. 产物要能被安装器下载：这里顺手打 zip 并打印 sha256。

前置：`uv tool install ppx-py==6.0.1`。用法：

    ./.venv/Scripts/python.exe .harness/scripts/build_editor.py
    ./.venv/Scripts/python.exe .harness/scripts/build_editor.py --console   # 带控制台，排错用

产物：`dist/RPA-Editor-<version>-win64.zip`（顶层目录 `RPA Probe/`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / ".harness" / "demo" / "ppx_probe" / "app"
RESOURCES = APP / "api" / "resources"
BUILD = APP / "build"
DIST = ROOT / "dist"


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    print("$", " ".join(str(a) for a in argv), flush=True)
    return subprocess.run(argv, cwd=str(cwd), env=env, check=False).returncode


def _ppx_python() -> Path | None:
    """ppx-py 那个 uv tool 环境里的 python（PyInstaller 跑在它里面）。"""
    try:
        out = subprocess.run(
            ["uv", "tool", "dir"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = Path(out.stdout.strip())
    for rel in (("ppx-py", "Scripts", "python.exe"), ("ppx-py", "bin", "python")):
        candidate = root.joinpath(*rel)
        if candidate.is_file():
            return candidate
    return None


def _ensure_build_deps(python: Path) -> None:
    """构建环境必须能 import rpa_core 的依赖，否则 PyInstaller 只会打 WARNING 出坏包。"""
    probe = "import pydantic, jsonschema; print(pydantic.VERSION)"
    done = subprocess.run([str(python), "-c", probe], capture_output=True, text=True, check=False)
    if done.returncode == 0:
        print(f"[deps] pydantic {done.stdout.strip()} + jsonschema 已在构建环境")
        return
    print("[deps] 构建环境缺 pydantic/jsonschema，安装（只这两个，不拉执行器依赖）", flush=True)
    code = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "pydantic>=2.10,<3",
            "jsonschema>=4.23,<5",
        ],
        cwd=ROOT,
    )
    if code != 0:
        raise SystemExit("[deps] 安装失败：PyInstaller 会打出一个缺依赖的包")


def _stage_resources() -> None:
    """把 rpa_core 的数据文件摆到 api/resources（ppx 会把它收成 `_MEIPASS/resources`）。"""
    if RESOURCES.exists():
        shutil.rmtree(RESOURCES)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    src_commands = ROOT / "commands"
    if not src_commands.is_dir():
        raise SystemExit(f"[stage] 找不到命令目录：{src_commands}")
    shutil.copytree(src_commands, RESOURCES / "commands")
    i18n = ROOT / "src" / "rpa_core" / "gui" / "i18n.js"
    if not i18n.is_file():
        raise SystemExit(f"[stage] 找不到 i18n.js：{i18n}")
    shutil.copy2(i18n, RESOURCES / "i18n.js")
    count = len(list((RESOURCES / "commands").rglob("*.json")))
    print(f"[stage] commands {count} 个 json + i18n.js → api/resources")


def _zip(version: str, console: bool) -> Path:
    # ppx build 的产物是 `build/RPA Probe/`，便携目录直接压缩即可（解压即用）。
    payload = BUILD / "RPA Probe"
    if not payload.is_dir():
        raise SystemExit(f"[zip] 找不到构建产物：{payload}")
    DIST.mkdir(parents=True, exist_ok=True)
    suffix = "-console" if console else ""
    stem = DIST / f"RPA-Editor-{version}-win64{suffix}"
    archive = shutil.make_archive(str(stem), "zip", root_dir=str(BUILD), base_dir="RPA Probe")
    path = Path(archive)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    print(f"[zip] {path}  {size / 1024 / 1024:.1f} MB  sha256={digest}")
    print(json.dumps({"zip": str(path), "bytes": size, "sha256": digest}, ensure_ascii=False))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建可分发的编辑窗口 zip")
    parser.add_argument("--console", action="store_true", help="保留控制台输出（排错用）")
    parser.add_argument("--skip-frontend", action="store_true", help="前端产物已是最新时复用")
    args = parser.parse_args()

    if shutil.which("ppx") is None:
        print("找不到 ppx。先装： uv tool install ppx-py==6.0.1", file=sys.stderr)
        return 2
    python = _ppx_python()
    if python is None:
        print("找不到 ppx-py 的构建环境（uv tool dir 里没有 ppx-py）", file=sys.stderr)
        return 2
    _ensure_build_deps(python)
    _stage_resources()

    env = dict(os.environ)
    # PyInstaller 靠 sys.path 找 rpa_core；ppx 的 spec 里 pathex 只有 app 根。
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    argv = ["ppx", "build"]
    if args.console:
        argv.append("--console")
    if args.skip_frontend:
        argv.append("--skip-frontend")
    code = _run(argv, cwd=APP, env=env)
    if code != 0:
        print(f"ppx build 失败（退出码 {code}）", file=sys.stderr)
        return code

    version = "0.0.0"
    for line in (APP / "ppx.toml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            version = line.split("=", 1)[1].strip().strip('"')
            break
    _zip(version, args.console)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
