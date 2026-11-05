"""catalog 语义投影：实测驱动的三条判据的负向验证。

背景：先做「实测消费」（照 `catalog --view agent` 手写 workflow.json 并真跑），
暴露了三个投影缺口——跨字段约束（oneOf）丢失、array 元素类型丢失、example
被当成可跑样例。补上判据后，本探针注入破坏证明它们**真的会红**。

三处注入都是**合法语法的精准放宽**（不制造语法错，只削弱语义），
每轮还原后核 md5 逐字节一致，末尾复跑基线确认复绿。

用法：`.venv/Scripts/python.exe .harness/spike/probe_agent_view_negative.py`
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "src" / "rpa_core" / "catalog" / "agent_view.py"
TEST_FILE = "tests/unit/test_catalog_agent_view.py"
SENTINEL = "负向验证注入点"
PYTHON = str(REPO / ".venv" / "Scripts" / "python.exe")

INJECTIONS = [
    {
        "name": "跨字段约束不透传（oneOf 丢失）",
        "old": "    return {key: input_schema[key] for key in _SCHEMA_CONSTRAINT_KEYS if key in input_schema}\n",
        "new": f"    return {{}}  # {SENTINEL}\n",
        "expect_fail": {"test_cross_field_constraints_are_projected"},
    },
    {
        "name": "array 元素类型不透传",
        "old": '    if isinstance(prop.get("items"), dict) and prop["items"].get("type"):\n',
        "new": f"    if False:  # {SENTINEL}\n",
        "expect_fail": {"test_array_param_items_are_projected"},
    },
    {
        "name": "example 边界说明被清空",
        "old": '        "example_contract": _EXAMPLE_NOTE,\n',
        "new": f'        "example_contract": "",  # {SENTINEL}\n',
        "expect_fail": {"test_example_contract_is_published"},
    },
]


def run_tests() -> tuple[int, set[str]]:
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", TEST_FILE, "-q", "--no-header"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = proc.stdout + proc.stderr
    failed = {
        line.split("::")[1].split(" ")[0]
        for line in output.splitlines()
        if line.startswith("FAILED")
    }
    return proc.returncode, failed


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if SENTINEL in source:
        print("哨兵残留：上次运行没有还原干净，拒绝继续")
        return 2
    original = TARGET.read_bytes()
    baseline_md5 = hashlib.md5(original).hexdigest()

    rc, failed = run_tests()
    if rc != 0:
        print(f"基线就是红的（rc={rc} failed={sorted(failed)}），先修基线")
        return 2
    print(f"[PASS] 基线绿（md5={baseline_md5[:8]}）")

    problems: list[str] = []
    try:
        for item in INJECTIONS:
            text = TARGET.read_text(encoding="utf-8")
            hits = text.count(item["old"])
            if hits != 1:
                problems.append(f"{item['name']}：锚点命中 {hits} 次（要求唯一）")
                print(f"[FAIL] {item['name']}：锚点命中 {hits} 次")
                continue
            TARGET.write_bytes(text.replace(item["old"], item["new"]).encode("utf-8"))
            if SENTINEL not in TARGET.read_text(encoding="utf-8"):
                problems.append(f"{item['name']}：注入未落盘")
                print(f"[FAIL] {item['name']}：注入未落盘")
            else:
                rc, failed = run_tests()
                ok = rc != 0 and failed == item["expect_fail"]
                print(
                    f"[{'PASS' if ok else 'FAIL'}] 注入「{item['name']}」"
                    f" → rc={rc} 红={sorted(failed)}"
                )
                if not ok:
                    problems.append(
                        f"{item['name']}：期望 {sorted(item['expect_fail'])} 实际 {sorted(failed)}"
                    )
            TARGET.write_bytes(original)
            if hashlib.md5(TARGET.read_bytes()).hexdigest() != baseline_md5:
                problems.append(f"{item['name']}：还原失败")
                print(f"[FAIL] {item['name']}：还原失败")

        rc, failed = run_tests()
        if rc != 0 or failed:
            problems.append(f"还原后基线未复绿：rc={rc} failed={sorted(failed)}")
            print(f"[FAIL] 还原后基线未复绿 rc={rc}")
        else:
            print("[PASS] 逐字节还原（md5 一致）且基线复绿")
    finally:
        if hashlib.md5(TARGET.read_bytes()).hexdigest() != baseline_md5:
            TARGET.write_bytes(original)
            print("[兜底] 已从内存副本还原")

    if problems:
        print("\n未通过：")
        for problem in problems:
            print("  -", problem)
        return 1
    print("\n全部通过：三条注入都被判据拦住，源码逐字节还原。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
