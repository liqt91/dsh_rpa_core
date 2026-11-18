"""M77 负向验证：图像 proposal 必须是「显式选择 + 不冒充实测」。

A. CLI/API/UI 的开关必须端到端透传；
B. 只有 prefer_image 才产 image locator；
C. proposal 的 matchedCount 恒为 0，driver/backend 恒为 image。

纪律同 M76：哨兵自检、锚点唯一、try/finally 逐字节还原核 md5、不接管道。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_m77_image_capture_negative.py
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
DESKTOP = ROOT / "src/rpa_core/capture/desktop.py"
AGENT = ROOT / "src/rpa_core/capture/desktop_agent.py"
PROPOSAL = ROOT / "src/rpa_core/capture/image_proposal.py"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
HTML = ROOT / ".harness/demo/ppx_probe/app/gui/index.html"
JS = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
TEST = ROOT / "tests/contract/test_image_capture_proposal.py"
MJS = ROOT / "scripts/check_element_library.mjs"
PICK_UI = ROOT / "scripts/check_element_pick_ui.mjs"

A_CLI = 'image=getattr(args, "image", False),'
B_GUARD = "if prefer_image and image_point is not None:"
C_DRIVER = '"driver": "image",'
D_API = "session_factory=lambda: build_hybrid_session(image=bool(image)),"
E_JS = "      image,\n"
F_HTML = 'id="elemCaptureImage"'
G_MJS = "按图捕获显式透传 image=true"
H_PICK_UI = "结构层：元素面板只有两个捕获按钮"

FILES = (CLI, DESKTOP, AGENT, PROPOSAL, API, HTML, JS, TEST, MJS, PICK_UI)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def check_static() -> None:
    assert A_CLI in CLI.read_text(encoding="utf-8"), "CLI 没透传 image"
    assert "args += [\"--image\"]" in DESKTOP.read_text(encoding="utf-8"), "会话没传 --image"
    agent = AGENT.read_text(encoding="utf-8")
    assert "prefer_image=args.image" in agent, "agent 没接 --image"
    assert B_GUARD in agent, "显式守卫不存在"
    proposal = PROPOSAL.read_text(encoding="utf-8")
    assert proposal.count(C_DRIVER) == 1, "proposal driver 不唯一"
    assert '"proposal": True' in proposal, "proposal 标记缺失"
    assert D_API in API.read_text(encoding="utf-8"), "RPC 没透传 image"
    assert E_JS in JS.read_text(encoding="utf-8"), "前端没透传 image"
    assert F_HTML in HTML.read_text(encoding="utf-8"), "按图捕获按钮不存在"
    assert G_MJS in MJS.read_text(encoding="utf-8"), "UI 判据没钉按图捕获"
    assert H_PICK_UI in PICK_UI.read_text(encoding="utf-8"), "拾取 UI 判据没钉两个按钮"


def _run_tests() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = "--basetemp=C:/Users/Administrator/AppData/Local/Temp/rpa-core-m77-neg"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TEST),
         str(ROOT / "tests/contract/test_cli_parity.py"), "-k",
         "image or image_capture or cli_capture_desktop_image_flag", "-o", "addopts="],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
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
            code, out = _run_tests()
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
    try:
        check_static()
    except AssertionError as exc:
        raise SystemExit(f"哨兵自检失败：{exc}") from exc
    code, out = _run_tests()
    if code != 0:
        raise SystemExit(f"对照跑没绿：\n{out[-1500:]}")

    _expect_red("A1 CLI 不透传", (CLI, A_CLI, "image=False,"), run_tests=True)
    _expect_red("B1 去掉显式守卫", (AGENT, B_GUARD, "if False:"), run_tests=True)
    _expect_red("C1 driver 改语义", (PROPOSAL, C_DRIVER, '"driver": "semantic",'), run_tests=True)
    _expect_red("D1 RPC 不透传", (API, D_API, "session_factory=lambda: build_hybrid_session(),"),
                run_tests=False)
    _expect_red("E1 前端不透传", (JS, E_JS, "      image: false,\n"), run_tests=False)
    _expect_red("F1 去掉按钮", (HTML, F_HTML, 'id="elemCaptureSemantic"'), run_tests=False)
    _expect_red("G1 放松 UI 判据", (MJS, G_MJS, "按图捕获不校验透传"), run_tests=False)
    _expect_red("H1 放松按钮数", (PICK_UI, H_PICK_UI, "结构层：元素面板只有一个捕获按钮"),
                run_tests=False)

    check_static()
    print("M77 NEGATIVE VERIFICATION PASSED (8/8)")


if __name__ == "__main__":
    main()
