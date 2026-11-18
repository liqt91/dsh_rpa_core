"""M82 负向验证：把「图像元素执行面」的接线和判据逐个拆掉，确认测试精确变红。

纪律（沿用 .harness/spike/probe_m80_negative.py）：
* 每个注入点先自检**锚点唯一**（count == 1），不唯一就拒绝运行；
* 注入 → 跑 `tests/contract/test_desktop_image_element.py` → 逐条比对**期望失败集**
  （集合相等，不是「红了几条」）；
* 每次注入后 `finally` 逐字节还原并核 md5，绝不把注入留在源码里；
* 只用 `-v` 输出解析 `failed` / `error`，不认 `-q` 的汇总行（解析不稳）。

期望失败集是**按推理写的**，不是抄实际输出：跑完若不符，先怀疑期望写错。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = "tests/contract/test_desktop_image_element.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = Path("D:/Temp/m82_negative.log")

DESKTOP = "src/rpa_core/executors/desktop.py"
IMAGE_TARGET = "src/rpa_core/executors/image_target.py"

# 长行拆出来单独存：锚点必须与源码逐字一致，但脚本自己也要过 E501。
_ACTION_ARGS = "invocation, session, session_id, element_id, command, inputs, locator_data"

# (说明, 文件, 原文, 替换为, 期望失败集)
# 期望集的形态："FAILED::<test name>" / "ERROR::<test name>"
INJECTIONS = [
    (
        "拆掉 uia 元素动作分支的图像分派",
        DESKTOP,
        "            if is_image_locator(locator_data):\n"
        "                return action_result(\n"
        f"                    {_ACTION_ARGS}\n"
        "                )\n",
        "",
        {
            # 直接调 _execute_sync ⇒ ValidationError 原样抛出（pytest 把测试体的异常
            # 记成 failed，不是 error——这里按实际分类写，不美化）
            "FAILED::test_uia_executor_dispatches_image_element"
        },
    ),
    (
        "拆掉 uia findElement 的图像分派",
        DESKTOP,
        "                if is_image_locator(inputs[\"locator\"]):\n"
        "                    return find_element_result(\n"
        "                        invocation, session, session_id, inputs[\"locator\"], inputs\n"
        "                    )\n",
        "",
        {"FAILED::test_uia_find_element_accepts_image_locator"},
    ),
    (
        "把点击中心改错（width//2 → width//3）",
        IMAGE_TARGET,
        "        offset = (width // 2, height // 2)",
        "        offset = (width // 3, height // 3)",
        {
            "FAILED::test_click_center_is_rect_middle",
            "FAILED::test_click_image_evidence_records_layer_and_scale",
            "FAILED::test_action_click_uses_rect_and_writes_evidence",
        },
    ),
    (
        "辅助键抬起被跳过（只按下不抬起）",
        IMAGE_TARGET,
        "        for sequence in up_sequences:\n            press_keys(sequence)",
        "        for sequence in ():\n            press_keys(sequence)",
        {"FAILED::test_modifiers_are_released_even_when_click_raises"},
    ),
    (
        "拆掉图像层自己的守门（显式判定 + 形状校验一起放宽）",
        IMAGE_TARGET,
        "    if not is_image_locator(locator):\n"
        "        raise ImageTargetError(\n"
        '            _SHAPE_MESSAGES["image-not-explicit"],\n'
        '            code="INVALID_INPUT",\n'
        '            details={"reason": "image-not-explicit"},\n'
        "        )\n"
        "    shape_errors = image_locator_errors(locator)\n",
        "    shape_errors = []\n",
        {
            # 守门一拆：语义 locator 也会去调图像定位器（ADR 0003 红线失守），
            # 且 V1 模板不再被拒——两条判据同时失去保护。
            "FAILED::test_semantic_locator_never_calls_image_locator",
            "FAILED::test_v1_template_rejected_without_matching",
        },
    ),
]

_FAILED_RE = re.compile(r"^(FAILED|ERROR) (\S+?)::(\S+?)(?: |$)", re.MULTILINE)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_suite() -> tuple[set[str], str]:
    """跑目标测试文件，返回 (失败集, 原始输出的尾部)。"""
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", TARGET, "-v", "--no-header", "-p", "no:cacheprovider"],
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
    return reds, output


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
            reds, output = _run_suite()
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
            restored = _md5(path) == before
        if not restored:
            failures.append(f"{label}: 还原后 md5 不一致")
            break
        if reds != expected:
            failures.append(
                f"{label}: 期望 {sorted(expected)} 实际 {sorted(reds)}"
            )
            continue
        errored = sum(1 for red in reds if red.startswith("ERROR::"))
        print(f"OK  {label}: 精确红 {len(reds)} 条（error {errored}）")

    # 还原后必须整片绿——否则说明「还原」本身没恢复原状
    reds, output = _run_suite()
    if reds:
        failures.append(f"还原后仍有红：{sorted(reds)}")
    else:
        print("OK  还原后全绿")

    if failures:
        for item in failures:
            print("FAIL", item)
        return 1
    print("M82 NEGATIVE VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
