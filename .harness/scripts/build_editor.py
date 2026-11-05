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

    ./.venv/Scripts/python.exe .harness/scripts/build_editor.py --release
    ./.venv/Scripts/python.exe .harness/scripts/build_editor.py --console   # 带控制台，排错用

产物：`dist/RPA-Editor-win64.zip`（顶层目录 `RPA Probe/`，内含 `editor-version.json`）
及其 `.sha256`。

注意：这里**不**调 `ppx build`。`ppx build` 不带 `--console` 时，PyInstaller 打完便携
目录还会去调 Inno Setup 做安装包，本机/CI 通常没有 `ISCC.exe`，于是返回 1 —— 但我们要
的便携目录其实已经好了。靠「识别这条失败消息」判成功太脆，所以直接让 ppx 生成 spec，
再自己跑 PyInstaller。
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
    """ppx-py 那个 uv tool 环境里的 python（PyInstaller 跑在它里面）。

    CI 由 workflow 显式写 RPA_PPX_PYTHON；本地才自己去 uv tool dir 里找。
    不能把 `ppx-py` 这个目录名当前提：uv 版本不同可能改名，那样构建会在
    进 PyInstaller 之前就静默退出（首次 CI 只跑了 1 秒就是这么红的）。
    """
    explicit = os.environ.get("RPA_PPX_PYTHON")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"[ppx] RPA_PPX_PYTHON 指向的 python 不存在：{path}")
        print(f"[ppx] 用 RPA_PPX_PYTHON = {path}")
        return path
    try:
        out = subprocess.run(
            ["uv", "tool", "dir"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[ppx] 跑 `uv tool dir` 失败：{exc}", file=sys.stderr)
        return None
    root = Path(out.stdout.strip())
    if not root.is_dir():
        print(f"[ppx] uv tool dir 不是目录：{root!r}（stdout={out.stdout!r}）", file=sys.stderr)
        return None
    for rel in (("ppx-py", "Scripts", "python.exe"), ("ppx-py", "bin", "python")):
        candidate = root.joinpath(*rel)
        if candidate.is_file():
            return candidate
    # 兜底按结构认，不按名字认：任何 tool venv 里带解释器都比直接放弃强。
    for pattern in ("*/Scripts/python.exe", "*/bin/python"):
        for candidate in sorted(root.glob(pattern)):
            if candidate.is_file():
                print(f"[ppx] tool 目录名不是 ppx-py，按结构找到 {candidate}")
                return candidate
    tools = sorted(p.name for p in root.iterdir() if p.is_dir())
    print(
        f"[ppx] 在 {root} 里没找到 tool 环境的 python；现有 tool：{tools}",
        file=sys.stderr,
    )
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


def _version() -> str:
    """版本号只认 ppx.toml —— 资源名不带版本（release 用固定 URL），别在别处再抄一份。"""
    for line in (APP / "ppx.toml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("[zip] ppx.toml 里找不到 version")


def _ppx_spec(python: Path, *, console: bool, env: dict[str, str]) -> Path:
    """让 ppx 自己生成 spec（不在本脚本里复刻它的路径/资源规则）。

    `create_spec` 顺带写出 PyInstaller 要用的入口文件 `build/cache/ppx_entry.py`，
    所以这一步不能省，只拿 spec 路径拼字符串是不够的。
    """
    code = (
        "from ppx_py.packaging import create_spec\n"
        "from ppx_py.project import find_project_root, load_settings\n"
        "import sys\n"
        "root = find_project_root()\n"
        "print(create_spec(root, load_settings(root), sys.argv[1] == '1'))\n"
    )
    done = subprocess.run(
        [str(python), "-c", code, "1" if console else "0"],
        cwd=str(APP),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"[spec] ppx 生成 spec 失败：{(done.stderr or done.stdout).strip()[:500]}")
    spec = APP / "build" / "cache" / "ppx.spec"
    if not spec.is_file():
        raise SystemExit(f"[spec] 找不到 {spec}")
    print(f"[spec] console={console} → {spec}")
    return spec


def _build_portable(
    python: Path, *, console: bool, skip_frontend: bool, env: dict[str, str]
) -> int:
    """打便携目录，不做安装包（我们分发的就是 zip，不需要 Inno Setup）。"""
    if not skip_frontend:
        code = _run([shutil.which("pnpm") or "pnpm", "-C", "gui", "run", "build"], cwd=APP, env=env)
        if code != 0:
            return code
    spec = _ppx_spec(python, console=console, env=env)
    return _run(
        [
            str(python),
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(BUILD),
            "--workpath",
            str(BUILD / "cache" / "work"),
            str(spec),
        ],
        cwd=APP,
        env=env,
    )


def _write_version_marker(version: str) -> None:
    """包内版本清单：安装器从它读 version（release 的 URL 里没有版本号）。

    同时它也是一道结构判据 —— 一个随手塞了 exe 的 zip 不该被当成窗口装上。
    """
    payload = BUILD / "RPA Probe"
    if not payload.is_dir():
        raise SystemExit(f"[stage] 找不到构建产物：{payload}")
    marker = payload / "editor-version.json"
    marker.write_text(
        json.dumps(
            {"schemaVersion": 1, "version": version, "asset": "RPA-Editor-win64.zip"},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"[stage] editor-version.json → {version}")


def _zip(version: str, console: bool, release: bool = False) -> Path:
    # ppx build 的产物是 `build/RPA Probe/`，便携目录直接压缩即可（解压即用）。
    payload = BUILD / "RPA Probe"
    if not payload.is_dir():
        raise SystemExit(f"[zip] 找不到构建产物：{payload}")
    DIST.mkdir(parents=True, exist_ok=True)
    if release:
        # 发布用的名字必须**不带版本**：安装器走 releases/latest/download/<asset>，
        # 带版本号就得先查 API 解析 asset 名，多一次网络与一处可能不同步的解析。
        stem = DIST / "RPA-Editor-win64"
    else:
        suffix = "-console" if console else ""
        stem = DIST / f"RPA-Editor-{version}-win64{suffix}"
    archive = shutil.make_archive(str(stem), "zip", root_dir=str(BUILD), base_dir="RPA Probe")
    path = Path(archive)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    print(f"[zip] {path}  {size / 1024 / 1024:.1f} MB  sha256={digest}")
    if release:
        # 旁挂 .sha256：安装器下载完会顺手取它校验（取不到只警告，不阻断）。
        sidecar = Path(str(path) + ".sha256")
        sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8", newline="\n")
        print(f"[sha] {sidecar}")
    print(json.dumps({"zip": str(path), "bytes": size, "sha256": digest}, ensure_ascii=False))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="构建可分发的编辑窗口 zip")
    parser.add_argument("--console", action="store_true", help="保留控制台输出（排错用）")
    parser.add_argument("--skip-frontend", action="store_true", help="前端产物已是最新时复用")
    parser.add_argument(
        "--release",
        action="store_true",
        help="发布用：稳定文件名 RPA-Editor-win64.zip（无控制台）+ 旁挂 .sha256",
    )
    args = parser.parse_args()
    if args.release and args.console:
        print("--release 与 --console 互斥：发布包不该带黑框", file=sys.stderr)
        return 2

    # 本脚本只调 ppx-py 的 create_spec，不调 `ppx` 这个 CLI —— CI 上 `uv tool install`
    # 装完的可执行目录未必在 PATH，要求它只会造出假失败。真正必需的是那个 venv 里的 python。
    python = _ppx_python()
    if python is None:
        print(
            "找不到 ppx-py 的构建环境（uv tool dir 里没有 ppx-py）。"
            "先装： uv tool install ppx-py==6.0.1",
            file=sys.stderr,
        )
        return 2
    _ensure_build_deps(python)
    _stage_resources()

    env = dict(os.environ)
    # PyInstaller 靠 sys.path 找 rpa_core；ppx 的 spec 里 pathex 只有 app 根。
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    version = _version()
    code = _build_portable(
        python,
        console=args.console,
        skip_frontend=args.skip_frontend,
        env=env,
    )
    if code != 0:
        print(f"PyInstaller 失败（退出码 {code}）", file=sys.stderr)
        return code
    _write_version_marker(version)
    _zip(version, args.console, release=args.release)
    if args.release:
        print(
            "\n发布（release 里要同时传 zip 与 .sha256）：\n"
            f"  gh release create editor-v{version} "
            f"dist/RPA-Editor-win64.zip dist/RPA-Editor-win64.zip.sha256 "
            f"--title \"Editor {version}\" --notes \"编辑窗口 {version}\""
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
