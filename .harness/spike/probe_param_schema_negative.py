"""负向验证：编译期「节点参数 vs manifest input_schema」这条判据**真的拦得住**吗。

做法与 `probe_host_boundary.py` 同款：注入 → 必须红 → 逐字节还原 → 必须复绿。

★ 期望失败集是**按推理写死的**，不照抄实际输出：关掉判据后，只有「拒绝」类的
  三条应该变红，「放行」类的两条必须仍然绿（它们证明的是判据没有过度收紧）。
★ 注入点在**调用处**（`_validate_node_inputs(node, manifest)`），不是函数体内部：
  函数还在、只是不再被调用，这才等价于「这条判据没上线」。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "rpa_core" / "compiler" / "compiler.py"
CALL_LINE = "                _validate_node_inputs(node, manifest)\n"
INJECTED = "                # _validate_node_inputs(node, manifest)  # INJECT-OFF\n"

EXPECTED_RED = {
    "test_compile_rejects_parameter_not_in_schema",
    "test_compile_rejects_missing_required_parameter",
}
EXPECTED_GREEN = {
    "test_compile_accepts_element_ref_covering_required_parameter",
    "test_compile_defers_value_validation_to_runtime",
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def run_protocols() -> tuple[int, set[str], set[str], str]:
    proc = subprocess.run(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "pytest",
            "tests/unit/test_protocols.py",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-rf",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    failed = set()
    passed = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAILED "):
            failed.add(line.split("::")[-1].split(" ")[0])
        elif line.startswith("PASSED "):
            passed.add(line.split("::")[-1].split(" ")[0])
    return proc.returncode, failed, passed, proc.stdout


def main() -> int:
    source = TARGET.read_bytes()
    baseline_md5 = hashlib.md5(source).hexdigest()
    text = source.decode("utf-8")

    # 哨兵自检：源码形状不是预期就拒绝运行，避免把注入留在产品代码里。
    if INJECTED in text:
        print("ABORT: 源码里已有注入标记，先人工还原")
        return 2
    if text.count(CALL_LINE) != 1:
        print(f"ABORT: 调用处出现 {text.count(CALL_LINE)} 次（期望 1 次），锚点不唯一")
        return 2

    print(f"基线 md5 = {baseline_md5}")

    # 0) 未注入时应当全绿（否则后面的「变红」说明不了任何事）
    rc, failed, _passed, out = run_protocols()
    check("未注入：编译协议测试全绿", rc == 0 and not failed, f"rc={rc} failed={sorted(failed)}")
    if rc != 0:
        print(out[-1500:])
        return 1

    try:
        TARGET.write_bytes(text.replace(CALL_LINE, INJECTED).encode("utf-8"))
        print("已注入：注释掉 _validate_node_inputs 的调用")
        rc, failed, passed, out = run_protocols()
    finally:
        TARGET.write_bytes(source)

    check("注入后 pytest 非零", rc != 0, f"rc={rc}")
    check(
        "注入后变红 == 期望的「拒绝类」两条",
        failed == EXPECTED_RED,
        f"实际 {sorted(failed)}",
    )
    check(
        "注入后「放行类」两条仍绿（判据没有过度收紧）",
        EXPECTED_GREEN & failed == set(),
        f"误伤 {sorted(EXPECTED_GREEN & failed)}",
    )
    check(
        "注入后没有 collection error（是断言红，不是导入失败）",
        not any(line.startswith("ERROR ") for line in out.splitlines()),
    )

    restored_md5 = hashlib.md5(TARGET.read_bytes()).hexdigest()
    check("还原后 md5 与基线一致", restored_md5 == baseline_md5, f"{restored_md5}")

    rc, failed, _passed, _out = run_protocols()
    check("还原后复绿", rc == 0 and not failed, f"rc={rc} failed={sorted(failed)}")

    print(f"\n{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
