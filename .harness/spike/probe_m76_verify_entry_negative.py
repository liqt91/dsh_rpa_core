"""M76 负向验证：CLI/PPX 校验入口的判据真的拦得住。

三条用户路径都要钉住：
  A. `elements verify --live` 必须走到 M74 门面（删分支/删 flag 必红）；
  B. PPX 资产行按钮必须调 `rpa.verifyElement`（改 RPC 名必红）；
  C. RPC 必须调同一个门面且透传 mode（改注册名/写死 mode 必红）。

纪律：不接管道；哨兵自检；try/finally 逐字节还原并核 md5。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_m76_verify_entry_negative.py
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "src/rpa_core/cli.py"
TEST = ROOT / "tests/contract/test_cli_parity.py"
API = ROOT / "apps/editor/api/api.py"
JS = ROOT / "apps/editor/gui/src/main.js"
MJS = ROOT / "scripts/check_element_library.mjs"

CLI_LIVE = 'if getattr(args, "live", False):'
CLI_FLAG = 'el.add_argument(\n                        "--live",'
JS_RPC = "ppx.call('rpa.verifyElement', { path: currentPath, name })"
PY_REG = '@api_method("rpa.verifyElement")'
PY_CALL = "_verify(doc, mode=mode, want_shot=want_shot)"
MJS_RPC = "ppx.call('rpa.verifyElement'"
MJS_COUNT = "assert(labels.length === 3,"

FILES = (CLI, TEST, API, JS, MJS)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def check_static() -> None:
    js = JS.read_text(encoding="utf-8")
    py = API.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    assert JS_RPC in js, "JS 按钮没有调 rpa.verifyElement"
    assert "verify: '校验'," in js, "ELEM_TEXT 缺校验按钮文案"
    assert PY_REG in py, "RPC 注册名不对"
    assert PY_CALL in py, "RPC 没有透传 mode 到统一门面"
    mjs = MJS.read_text(encoding="utf-8")
    assert MJS_RPC in mjs, "UI 判据没钉校验 RPC"
    assert MJS_COUNT in mjs, "UI 判据没钉三个动作"
    assert CLI_LIVE in cli, "CLI 活体分支不存在"
    assert '"--live",' in cli, "CLI --live 参数不存在"
    assert "def test_cli_elements_live_verify_uses_unified_facade" in TEST.read_text(
        encoding="utf-8"
    ), "CLI 活体契约测试不存在"


def _run_cli_tests() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = "--basetemp=C:/Users/Administrator/AppData/Local/Temp/rpa-core-m76-neg"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(TEST),
            "-k",
            "elements_live or elements_list_show_verify",
            "-o",
            "addopts=",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _expect_red(name: str, patch: tuple[Path, str, str], *, run_tests: bool) -> None:
    before = {p: _md5(p) for p in FILES}
    path, old, new = patch
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"{name}: 锚点不唯一（count={source.count(old)}）")
    try:
        path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")
        if run_tests:
            code, out = _run_cli_tests()
            assert code != 0, f"{name}: 注入后仍绿"
            assert not re.search(r"\b\d+\s+errors?\b", out, re.IGNORECASE) and "ERROR " not in out, (
                f"{name}: 红来自 error 而非 failed\n{out[-1200:]}"
            )
            assert "failed" in out.lower(), f"{name}: 没有 failed 汇总\n{out[-1200:]}"
        else:
            try:
                check_static()
            except AssertionError:
                return
            raise AssertionError(f"{name}: 静态判据没红")
    finally:
        path.write_text(source, encoding="utf-8", newline="\n")
        for p, digest in before.items():
            assert _md5(p) == digest, f"{name}: {p.name} 还原后 md5 不一致"


def main() -> None:
    for path in FILES:
        if not path.is_file():
            raise SystemExit(f"缺文件：{path}")
    try:
        check_static()
    except AssertionError as exc:
        raise SystemExit(f"哨兵自检失败：{exc}") from exc

    code, out = _run_cli_tests()
    if code != 0:
        raise SystemExit(f"对照跑没绿：\n{out[-1500:]}")

    _expect_red("A1 删活体分支", (CLI, CLI_LIVE, "if False:"), run_tests=True)
    _expect_red("A2 删 --live flag", (CLI, '"--live",', '"--dead-live",'), run_tests=True)
    _expect_red("B1 改按钮 RPC", (JS, "rpa.verifyElement", "rpa.notVerifyElement"), run_tests=False)
    _expect_red("B2 改 UI 判据 RPC", (MJS, MJS_RPC, "ppx.call('rpa.notVerifyElement'"), run_tests=False)
    _expect_red("B3 放松动作数", (MJS, MJS_COUNT, "assert(labels.length === 2,"), run_tests=False)
    _expect_red("C1 改 RPC 注册名", (API, PY_REG, '@api_method("rpa.notVerifyElement")'), run_tests=False)
    _expect_red("C2 写死 mode", (API, PY_CALL, '_verify(doc, mode="flash", want_shot=False)'),
                run_tests=False)

    check_static()
    print("M76 NEGATIVE VERIFICATION PASSED (7/7)")


if __name__ == "__main__":
    main()
