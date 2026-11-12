"""负向验证：流程控制插入分组 + 流程面板分支体修复的判据是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，跑一遍相关单测，
断言 **实际失败集 == 按推理写下的期望失败集**（不是 >=，也不是「有红就行」）。
跑完逐字节还原所有目标文件并核 md5；任何异常都在 finally 里还原。

跑法：./.venv/Scripts/python.exe .harness/spike/negative_control_flow.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

WD = "apps/xhs-workbench/src/workflow_doc.py"
API = ".harness/demo/ppx_probe/app/api/api.py"
BRIDGE = "apps/xhs-workbench/src/ppx_bridge.py"
JS = ".harness/demo/ppx_probe/app/gui/src/main.js"

T_WD = "tests/test_workflow_doc.py"
T_BR = "tests/test_ppx_bridge.py"

CASES = [
    {
        "id": "A",
        "desc": "if 只留 then，丢掉 else 分支体",
        "rel": WD,
        "old": '    "if": ("then", "else"),',
        "new": '    "if": ("then",),',
        "pytest": [T_WD],
        "expect": {f"{T_WD}::test_control_flow_branches_are_all_visited"},
    },
    {
        "id": "B",
        "desc": "try 只留 children，丢掉 catch 分支体",
        "rel": WD,
        "old": '    "try": ("children", "catch"),',
        "new": '    "try": ("children",),',
        "pytest": [T_WD],
        "expect": {f"{T_WD}::test_control_flow_branches_are_all_visited"},
    },
    {
        "id": "C",
        "desc": "容器集合清空，分支体不再缩进",
        "rel": WD,
        "old": 'CONTAINER_TYPES = frozenset(CONTAINER_BRANCHES) | {"loop", "parallel", "branch"}',
        "new": "CONTAINER_TYPES = frozenset()",
        "pytest": [T_WD],
        "expect": {
            f"{T_WD}::test_control_flow_branches_are_all_visited",
            f"{T_WD}::test_container_nodes_are_descended_into_not_listed",
        },
    },
    {
        "id": "D",
        "desc": "新插入的 if 不带 else 标准子结构",
        "rel": API,
        "old": '"if": {"type": "if", "condition": {"op": "truthy", "left": ""}, "then": [], "else": []},',
        "new": '"if": {"type": "if", "condition": {"op": "truthy", "left": ""}, "then": []},',
        "pytest": [T_BR],
        "expect": {f"{T_BR}::test_insert_control_node_writes_real_ast_and_exposes_branch_anchors"},
    },
    {
        "id": "E",
        "desc": "虚拟组行不再带 branch，前端拿不到分支锚点",
        "rel": API,
        "old": "            branch=branch,\n",
        "new": '            branch="",\n',
        "pytest": [T_BR],
        "expect": {f"{T_BR}::test_insert_control_node_writes_real_ast_and_exposes_branch_anchors"},
    },
    {
        "id": "F",
        "desc": "_insert_plan 忽略 branch，落点退回容器默认分支",
        "rel": API,
        "old": "    if branch:\n        if branch not in _NESTED:",
        "new": "    if False:\n        if branch not in _NESTED:",
        "pytest": [T_BR],
        "expect": {f"{T_BR}::test_insert_control_node_writes_real_ast_and_exposes_branch_anchors"},
    },
    {
        "id": "G",
        "desc": "新建流程的 id 退回带中文的 slug",
        "rel": BRIDGE,
        "old": '        "id": f"xhs-{ascii_slug}",',
        "new": '        "id": f"xhs-{slug}",',
        "pytest": [T_BR],
        "expect": {
            f"{T_BR}::test_create_flow_id_is_valid_for_chinese_name",
            f"{T_BR}::test_insert_control_node_writes_real_ast_and_exposes_branch_anchors",
        },
    },
    {
        "id": "H",
        "desc": "未知控制流 kind 被默默当成 if",
        "rel": API,
        "old": "    template = _CONTROL_TEMPLATES.get(kind)",
        "new": '    template = _CONTROL_TEMPLATES.get(kind, _CONTROL_TEMPLATES["if"])',
        "pytest": [T_BR],
        "expect": {f"{T_BR}::test_insert_control_node_rejects_unknown_kind_without_writing"},
    },
    {
        "id": "I",
        "desc": "控制流分组点击退回 rpa.insertNode",
        "rel": JS,
        "old": "    const rpc = command.control ? 'rpa.insertControlNode' : 'rpa.insertNode'",
        "new": "    const rpc = command.control ? 'rpa.insertNode' : 'rpa.insertNode'",
        # 带一条必绿的陪跑用例：单条用例全红时 0 passed，会被「环境级非零」判据误杀
        "pytest": [
            f"{T_BR}::test_editor_palette_wires_control_flow_group_to_new_rpc",
            f"{T_BR}::test_dispatch_envelope_and_method_not_found",
        ],
        "expect": {f"{T_BR}::test_editor_palette_wires_control_flow_group_to_new_rpc"},
    },
    {
        "id": "J",
        "desc": "指令栏不再显示「流程控制」分组",
        "rel": JS,
        "old": "el.cmds.append(mk('div', 'nsGroup', `流程控制 · ${controls.length}`))",
        "new": "el.cmds.append(mk('div', 'nsGroup', `其它 · ${controls.length}`))",
        # 带一条必绿的陪跑用例：单条用例全红时 0 passed，会被「环境级非零」判据误杀
        "pytest": [
            f"{T_BR}::test_editor_palette_wires_control_flow_group_to_new_rpc",
            f"{T_BR}::test_dispatch_envelope_and_method_not_found",
        ],
        "expect": {f"{T_BR}::test_editor_palette_wires_control_flow_group_to_new_rpc"},
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_pytest(args: list[str]) -> tuple[set[str], str, int]:
    try:
        done = subprocess.run(
            # pyproject 的 addopts 已有 -q；再传一个会变 -qq，pytest 就不打印
            # "N failed, M passed" 汇总行，探针没法区分「判据红」和「环境级非零」。
            [str(PY), "-m", "pytest", *args, "--tb=no", "-p", "no:cacheprovider"],
            cwd=APP,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        return set(), "TIMEOUT: pytest 挂住超过 240s", -1
    out = (done.stdout or "") + (done.stderr or "")
    failed = set()
    for line in out.splitlines():
        hit = re.match(r"^FAILED (\S+)", line.strip())
        if hit:
            # Windows 上 pytest 回显的是 tests\xxx.py，与 nodeid 的正斜杠口径不同
            failed.add(hit.group(1).replace("\\", "/"))
    return failed, out, done.returncode


def main() -> int:
    paths = {case["rel"]: ROOT / case["rel"] for case in CASES}
    for rel, path in paths.items():
        if not path.is_file():
            print(f"MISSING {rel}")
            return 2
    originals = {rel: path.read_bytes() for rel, path in paths.items()}
    digests = {rel: md5(data) for rel, data in originals.items()}

    # 哨兵自检：上一轮崩在半路会把注入态留在源码里，先发现，别在脏基线上跑。
    for case in CASES:
        text = originals[case["rel"]].decode("utf-8")
        if text.count(case["new"]) > 0:
            print(f"SENTINEL: {case['rel']} 里残留注入态（{case['id']}），先还原再跑")
            return 2
        if text.count(case["old"]) != 1:
            print(f"ANCHOR: {case['id']} 的 old 命中 {text.count(case['old'])} 次，须恰好 1 次")
            return 2

    bad = 0
    try:
        for case in CASES:
            rel = case["rel"]
            path = paths[rel]
            text = originals[rel].decode("utf-8")
            try:
                path.write_bytes(text.replace(case["old"], case["new"], 1).encode("utf-8"))
                failed, out, rc = run_pytest(case["pytest"])
                # 环境级非零（沙箱删除守卫 / 解释器崩）不算「判据红了」
                counts = re.search(r"(\d+) failed", out)
                has_error = re.search(r"\d+ error", out)
                has_passed = re.search(r"[1-9]\d* passed", out)
                clean = (
                    rc != 0
                    and counts is not None
                    and int(counts.group(1)) == len(failed)
                    and has_error is None
                    and has_passed is not None
                )
                ok = clean and failed == case["expect"]
                print(f"[{case['id']}] {'RED-OK' if ok else 'BAD'} {case['desc']}")
                print(
                    f"      rc={rc} clean={clean} counts={counts.group(1) if counts else None} "
                    f"error={has_error.group(0) if has_error else None} "
                    f"passed={has_passed.group(0) if has_passed else None}"
                )
                print(f"      expect={sorted(case['expect'])}")
                print(f"      actual={sorted(failed)}")
                if not clean:
                    print(f"      tail={out.strip()[-400:]}")
                if not ok:
                    bad += 1
            finally:
                path.write_bytes(originals[rel])
    finally:
        for rel, path in paths.items():
            path.write_bytes(originals[rel])
        for rel, path in paths.items():
            if md5(path.read_bytes()) != digests[rel]:
                print(f"RESTORE FAILED: {rel} 与基线 md5 不一致")
                bad += 1

    print(f"\nNEGATIVE VERIFY {'PASSED' if bad == 0 else 'FAILED'} ({len(CASES) - bad}/{len(CASES)})")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
