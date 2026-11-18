"""M84 负向验证：把「红框=模板」「让位按进程名」两组判据逐个拆掉。

纪律同 probe_m82/m83：锚点唯一自检 → 注入 → **集合相等**比对 → finally 逐字节还原核 md5；
只用 `-v` 解析 failed/error。

期望失败集按推理写，不照抄实际——写错就按实际逐条核对是「判据错」还是「期望错」。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [
    "tests/contract/test_browser_content_yield.py",
    "tests/contract/test_image_capture_proposal.py",
]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = Path("D:/Temp/m84_negative.log")

FOREGROUND = "src/rpa_core/capture/foreground_window.py"
AGENT = "src/rpa_core/capture/desktop_agent.py"
PROPOSAL = "src/rpa_core/capture/image_proposal.py"

INJECTIONS = [
    (
        "让位判定退回「只看类名」（WorkBuddy 报障前的原样）",
        FOREGROUND,
        "    return browser_of_window(root_hwnd, api=api) is not None",
        "    return True  # 注入：只看类名",
        {"FAILED::test_electron_client_is_not_browser_content"},
    ),
    (
        "图像主定位不再覆盖 preview 矩形（回到「框住整窗」）",
        AGENT,
        "    rect_text: dict | None = image_rect\n    if rect_text is None:\n",
        "    rect_text: dict | None = None\n    if rect_text is None:\n",
        {"FAILED::test_describe_info_image_rect_is_the_template_box"},
    ),
    (
        "点捕获不再带模板矩形",
        AGENT,
        '            best_count = 0\n'
        '            image_rect = _rect_from_source(image_record["locator"].get("sourceRect"))\n',
        "            best_count = 0\n",
        {"FAILED::test_capture_at_image_rect_is_the_template_box"},
    ),
    (
        "模板框不再裁到窗口内（贴边就截到隔壁窗口）",
        PROPOSAL,
        "    return (\n"
        "        max(left, px - radius),\n"
        "        max(top, py - radius),\n"
        "        min(right, px + radius),\n"
        "        min(bottom, py + radius),\n"
        "    )\n",
        "    return (px - radius, py - radius, px + radius, py + radius)\n",
        {"FAILED::test_clip_template_rect_is_the_shared_geometry"},
    ),
    (
        "图像模式恢复 4 轮重试（白跑 4 次 UIA + 4 张截图）",
        AGENT,
        "    if prefer_image:\n"
        "        # 图像模式的 ``verifyCount`` 恒 0（proposal 没跑过匹配，不冒充实测），\n"
        "        # 因而下面的「等到 1 就返回」永不成立——按原样会白跑 4 轮（每轮一次 UIA\n"
        "        # 命中 + 一张窗口截图，真机 200~900ms/轮）。一次就够。\n"
        "        return capture_at(x, y, scope_hwnd, prefer_image=True)\n",
        "    if False:\n"
        "        return capture_at(x, y, scope_hwnd, prefer_image=True)\n",
        {"FAILED::test_capture_with_retry_single_attempt_in_image_mode"},
    ),
]

_FAILED_RE = re.compile(r"^(FAILED|ERROR) (\S+?)::(\S+?)(?: |$)", re.MULTILINE)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_suite() -> set[str]:
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", *TARGETS, "-v", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout + completed.stderr
    LOG.write_text(output, encoding="utf-8")
    reds: set[str] = set()
    for kind, _file, name in _FAILED_RE.findall(output):
        reds.add(f"{kind}::{name.split('[')[0]}")
    return reds


def main() -> int:
    failures: list[str] = []
    for label, rel, old, new, expected in INJECTIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        before = _md5(path)
        occurrences = original.count(old)
        if occurrences != 1:
            failures.append(f"{label}: 锚点出现 {occurrences} 次（要求唯一），拒绝注入")
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
            reds = _run_suite()
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
            restored = _md5(path) == before
        if not restored:
            failures.append(f"{label}: 还原后 md5 不一致")
            break
        if reds != expected:
            failures.append(f"{label}: 期望 {sorted(expected)} 实际 {sorted(reds)}")
            continue
        print(f"OK  {label}: 精确红 {len(reds)} 条")

    reds = _run_suite()
    if reds:
        failures.append(f"还原后仍有红：{sorted(reds)}")
    else:
        print("OK  还原后全绿")

    if failures:
        for item in failures:
            print("FAIL", item)
        return 1
    print("M84 NEGATIVE VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
