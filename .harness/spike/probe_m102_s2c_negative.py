"""M102 S2c 负向验证：视口图**没有 imageSize** 时，消费侧必须自己量图。

现场（2026-11-20 维护者：「网页元素捕获后还是没有截图」）：
扩展 0.9.4 已重载、``capture_shot mark=viewport`` 也打了，但 trace 里
``element_shot`` 一行都没有。根因是 host 只挂 ``{dataUrl, space}``，
消费侧却要求 ``imageSize``，缺失时静默 ``return None``——连 trace 都不打。

本轮注入把「从 dataUrl 量尺寸」这一句换回 ``None``，验证四处一起红：

* ``test_element_shot_viewport_shot_without_image_size``（新增契约）
* ``probe_m102_s2b_web_shot.py`` 第 1 组真机视口图
* ``probe_m102_s1_wiring.py`` 端到端（_hold_captured → commit → 落盘）
* 纯 JS 通道判据**不该红**——bug 在 Python 消费侧，不是扩展没给图。

期望失败集按推理写死；跑完按原字节还原并核 md5；带哨兵防叠注入。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"
PPX_PY = ROOT / "apps/editor/.venv/Scripts/python.exe"
SHOT = ROOT / "src/rpa_core/capture/screen_shot.py"
TESTS = "tests/contract/test_element_shot.py"
T = TESTS + "::"
S2B = ".harness/spike/probe_m102_s2b_web_shot.py"
WIRING = ".harness/spike/probe_m102_s1_wiring.py"
NODE_CHECK = "scripts/check_capture_shot.mjs"
MARKER = "  # <<<M102C-INJECTED>>>"

OLD = '        image_size = data_url_image_size(shot.get("dataUrl"))'
NEW = "        image_size = None"
EXPECTED_PYTEST = {T + "test_element_shot_viewport_shot_without_image_size"}


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout + proc.stderr


def fail_set_from_pytest(output: str) -> set[str]:
    return {
        m.group(1).replace("\\", "/")
        for line in output.splitlines()
        if (m := re.match(r"FAILED\s+(\S+)", line.strip()))
    }


def fail_set_from_node(output: str) -> set[str]:
    return {
        m.group(1)
        for line in output.splitlines()
        if (m := re.match(r"FAIL\s+\|\s+(.+?)(?:\s+→.*)?$", line.strip()))
    }


def main() -> int:
    text = SHOT.read_text(encoding="utf-8")
    if "<<<M102C-INJECTED>>>" in text:
        print("[哨兵] screen_shot.py 里还有上次的注入标记，拒绝运行")
        return 2
    if text.count(OLD) != 1:
        print(f"[锚点] 出现 {text.count(OLD)} 次，必须恰好 1 次")
        return 2

    original = SHOT.read_bytes()
    before = md5(SHOT)
    restore_failed = False
    try:
        SHOT.write_bytes(text.replace(OLD, NEW + MARKER, 1).encode("utf-8"))
        got_pytest = fail_set_from_pytest(
            _run([str(PY), "-m", "pytest", "-q", "--tb=no", "-rf", TESTS]))
        got_node = fail_set_from_node(_run(["node", NODE_CHECK]))
        got_s2b = _run([str(PPX_PY), S2B])
        got_wiring = _run([str(PPX_PY), WIRING])
    finally:
        SHOT.write_bytes(original)
        if md5(SHOT) != before:
            print("[还原失败] 文件字节不一致")
            restore_failed = True
    if restore_failed:
        return 3

    diffs: list[str] = []
    if got_pytest != EXPECTED_PYTEST:
        diffs.append(f"pytest 缺失={sorted(EXPECTED_PYTEST - got_pytest)} "
                     f"多出={sorted(got_pytest - EXPECTED_PYTEST)}")
    if got_node:
        diffs.append(f"node 不该红={sorted(got_node)}")
    if "RESULT: PASS" in got_s2b:
        diffs.append("s2b 探针没红（仍是 PASS）")
    if "RESULT: PASS" in got_wiring:
        diffs.append("wiring 探针没红（仍是 PASS）")
    if diffs:
        print("FAIL M102 S2c: " + "；".join(diffs))
        return 1
    print(f"OK   image-size-from-data-url: pytest {len(got_pytest)} 条、node 0 条、"
          "s2b/wiring 红/红")
    print("\nM102 S2c NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
