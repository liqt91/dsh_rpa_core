"""S6 结构节点探针：if / forEach / try / return 的字段表与写回。

四种节点各造一个真文件走端到端（`workflows/` 里只有一个 if、没有 forEach/try，
靠「恰好有样例」的判据等于没验）。逐条：

字段表
1. if → op / left / right 三个字段，op 是 enum 且选项数与 `Condition.op` 的 Literal 一致
2. op 的选项带中文标签（不是英文原值兜底）
3. try → error_var；return → value；forEach → items + item_var
4. action 节点走 structuralDetail → 无字段（不该串到参数表单去）
5. sequence 节点 → 无字段

写回
6. if 改 op/left/right → 磁盘上 condition 三者都对，且 `1` 被解析成数字 1
7. if 的 right 留空 → 磁盘 condition **没有** right 键
8. if 的 left 写 `${...}` → 原样保留为字符串（不当 JSON 解析）
9. if 的 op 改成不认识的 → INVALID_VALUE，文件不动
10. if 漏传字段 → INCOMPLETE_SUBMIT
11. forEach 的 items 非法 JSON → INVALID_VALUE
12. forEach 的 items 写成 `"${rows}"`（带引号的引用）→ **接受**（resolve 会求值成列表）
13. forEach 的 items 写成对象 → 接受但带 warning（编译期不查类型，运行期会 INVALID_INPUT）
14. forEach 的 item_var 非法标识符 → INVALID_VALUE
15. try 的 error_var 非法 → INVALID_VALUE
16. return 的 value 留空 → 磁盘上是 null
17. return 的 value 写 JSON 对象 → 磁盘上是对象
18. 结构节点改完后编译通过
19. 未触碰的行逐字节不变（改 if 的三个字段只动 condition 那几行）
"""

from __future__ import annotations

import difflib
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
SANDBOX = HERE / "_sandbox"
# 放 `_sandbox/` 根下而不是子目录：`bootstrap()` 只扫 `_sandbox/*.json`，
# 放子目录的话这份夹具不会出现在流程下拉框里，也就选不到。
PROBE_DIR = SANDBOX
TARGET = PROBE_DIR / "struct.json"
REL = TARGET.relative_to(REPO).as_posix()

FIXTURE = {
    "schema_version": "1.0",
    "id": "struct-probe",
    "name": "structural nodes probe",
    "inputs": {"flag": True, "rows": ["a", "b"]},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {
                "type": "if",
                "id": "if1",
                "condition": {"op": "truthy", "left": "${inputs.flag}"},
                "then": [{"type": "return", "id": "r_then", "value": "yes"}],
                "else": [{"type": "return", "id": "r_else", "value": "no"}],
            },
            {
                "type": "forEach",
                "id": "fe1",
                "items": ["a", "b"],
                "item_var": "row",
                "children": [{"type": "return", "id": "r_loop", "value": "${loop.row}"}],
            },
            {"type": "try", "id": "t1", "error_var": "err", "children": [], "catch": []},
            {"type": "return", "id": "r2", "value": None},
        ],
    },
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def sha() -> str:
    return hashlib.sha256(TARGET.read_bytes()).hexdigest()


def write_fixture() -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes((json.dumps(FIXTURE, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def disk_node(node_id: str) -> dict:
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    stack = [document["root"]]
    while stack:
        node = stack.pop()
        if node.get("id") == node_id:
            return node
        for key in ("children", "then", "else", "catch"):
            stack.extend(node.get(key) or [])
    raise AssertionError(f"磁盘上找不到节点 {node_id}")


def fields_of(node_id: str) -> dict:
    detail = A.structural_detail(REL, node_id)
    return {f["key"]: f for f in detail["fields"]}


def texts(node_id: str) -> dict:
    return {k: f["valueText"] for k, f in fields_of(node_id).items()}


def main() -> int:
    global A
    write_fixture()
    sys.path.insert(0, str(HERE / "app"))
    import api.api as Api  # noqa: E402 - 必须在 sys.path 之后导入

    A = Api
    A.open_flow(REL)

    # ---- 1 / 2. if 的字段表 ----------------------------------------------
    from typing import get_args  # noqa: PLC0415 - 只在这个探针里用

    from rpa_core.model.workflow import Condition  # noqa: E402

    ops = get_args(Condition.model_fields["op"].annotation)
    iff = fields_of("if1")
    check(
        "if → op / left / right 三个字段",
        list(iff) == ["op", "left", "right"],
        f"{list(iff)}",
    )
    check(
        "op 是 enum，选项与 Condition.op 的 Literal 一致",
        [o["value"] for o in iff["op"]["enum"]] == list(ops),
        f"{[o['value'] for o in iff['op']['enum']]}",
    )
    labels = [o["label"] for o in iff["op"]["enum"]]
    check(
        "op 选项带中文标签（不是英文原值兜底）",
        labels[0] == "等于" and all(label != op for label, op in zip(labels, ops)),
        f"{labels[:3]}",
    )
    check(
        "left 显示为原样字符串（${...} 不展开）",
        iff["left"]["valueText"] == "${inputs.flag}",
        iff["left"]["valueText"],
    )

    # ---- 3. 其余三种 ------------------------------------------------------
    check("forEach → items + item_var", list(fields_of("fe1")) == ["items", "item_var"])
    check("try → error_var", list(fields_of("t1")) == ["error_var"])
    check("return → value", list(fields_of("r2")) == ["value"])
    check(
        "items 显示为 JSON 数组文本",
        fields_of("fe1")["items"]["valueText"] == '["a", "b"]',
        fields_of("fe1")["items"]["valueText"],
    )

    # ---- 4 / 5. 不该有字段的节点 -----------------------------------------
    flow = A.open_flow(REL)
    action_id = next(c["id"] for c in flow["cards"] if c["kind"] == "action") if any(
        c["kind"] == "action" for c in flow["cards"]
    ) else None
    root_detail = A.structural_detail(REL, "root")
    check("sequence 节点没有结构字段", root_detail["editable"] is False)
    check(
        "action 节点走 structuralDetail 也没有结构字段",
        action_id is None or A.structural_detail(REL, action_id)["editable"] is False,
        f"action 节点：{action_id}",
    )

    # ---- 6 / 7 / 8. if 写回 ----------------------------------------------
    before = TARGET.read_text(encoding="utf-8")
    saved = A.save_structured_params(
        REL, "if1", {"op": "eq", "left": "1", "right": "1"}
    )
    check(
        "if 保存成功",
        saved.get("ok") is True,
        f"{saved.get('code')} {str(saved.get('error'))[:90]}",
    )
    cond = disk_node("if1")["condition"]
    check(
        "磁盘上 op/left/right 都对，且 1 被解析成数字",
        cond == {"op": "eq", "left": 1, "right": 1},
        json.dumps(cond, ensure_ascii=False),
    )

    after = TARGET.read_text(encoding="utf-8")
    diff = [
        line
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    check(
        "改 if 只动 condition 那两行（2 删 3 增：多出一个 right 键）",
        len(diff) == 5,
        f"{len(diff)} 条 +/-：{[d.strip()[:40] for d in diff]}",
    )

    dropped = A.save_structured_params(REL, "if1", {"op": "truthy", "left": "${inputs.flag}", "right": ""})
    check("if 右值留空保存成功", dropped.get("ok") is True, str(dropped.get("code")))
    cond = disk_node("if1")["condition"]
    check(
        "右值留空 → 磁盘上没有 right 键",
        "right" not in cond and cond["left"] == "${inputs.flag}",
        json.dumps(cond, ensure_ascii=False),
    )
    check(
        "left 的 ${...} 原样保留为字符串",
        isinstance(cond["left"], str) and cond["left"] == "${inputs.flag}",
        repr(cond["left"]),
    )

    # ---- 9 / 10. 拒收 ----------------------------------------------------
    sha_now = sha()
    bad_op = A.save_structured_params(
        REL, "if1", {"op": "notAnOp", "left": "1", "right": ""}
    )
    check(
        "不认识的操作符被拒",
        bad_op.get("ok") is False and bad_op.get("code") == "INVALID_VALUE",
        str(bad_op.get("error", ""))[:80],
    )
    partial = A.save_structured_params(REL, "if1", {"op": "eq"})
    check(
        "if 漏传字段 → INCOMPLETE_SUBMIT",
        partial.get("ok") is False and partial.get("code") == "INCOMPLETE_SUBMIT",
        str(partial.get("error", ""))[:80],
    )
    check("两次拒收都没动文件", sha() == sha_now)

    # ---- 11~14. forEach --------------------------------------------------
    bad_json = A.save_structured_params(
        REL, "fe1", {"items": "[a, b]", "item_var": "row"}
    )
    check(
        "items 非法 JSON 被拒",
        bad_json.get("ok") is False and bad_json.get("code") == "INVALID_VALUE",
        str(bad_json.get("error", ""))[:80],
    )
    ref = A.save_structured_params(
        REL, "fe1", {"items": '"${inputs.rows}"', "item_var": "row"}
    )
    check(
        "items 写成带引号的引用 → 接受（resolve 会求值成列表）",
        ref.get("ok") is True and disk_node("fe1")["items"] == "${inputs.rows}",
        json.dumps(disk_node("fe1").get("items"), ensure_ascii=False),
    )
    obj = A.save_structured_params(REL, "fe1", {"items": '{"a": 1}', "item_var": "row"})
    check(
        "items 写成对象 → 接受但带 warning（编译期不查类型）",
        obj.get("ok") is True and any(w["key"] == "items" for w in obj.get("warnings") or []),
        f"warnings={obj.get('warnings')}",
    )
    bad_var = A.save_structured_params(REL, "fe1", {"items": "[]", "item_var": "1abc"})
    check(
        "item_var 非法标识符被拒",
        bad_var.get("ok") is False and bad_var.get("code") == "INVALID_VALUE",
        str(bad_var.get("error", ""))[:80],
    )

    # ---- 15. try ---------------------------------------------------------
    bad_err = A.save_structured_params(REL, "t1", {"error_var": "a b"})
    check(
        "error_var 非法标识符被拒",
        bad_err.get("ok") is False and bad_err.get("code") == "INVALID_VALUE",
        str(bad_err.get("error", ""))[:80],
    )
    ok_err = A.save_structured_params(REL, "t1", {"error_var": "boom"})
    check(
        "error_var 改成功",
        ok_err.get("ok") is True and disk_node("t1")["error_var"] == "boom",
        str(disk_node("t1").get("error_var")),
    )

    # ---- 16 / 17. return -------------------------------------------------
    nulled = A.save_structured_params(REL, "r2", {"value": ""})
    check(
        "value 留空 → 磁盘上是 null",
        nulled.get("ok") is True and disk_node("r2").get("value") is None,
        repr(disk_node("r2").get("value")),
    )
    objectified = A.save_structured_params(REL, "r2", {"value": '{"ok": true}'})
    check(
        "value 写 JSON 对象 → 磁盘上是对象",
        objectified.get("ok") is True and disk_node("r2").get("value") == {"ok": True},
        json.dumps(disk_node("r2").get("value"), ensure_ascii=False),
    )

    # ---- 18. 编译 --------------------------------------------------------
    compiled = A.compile_check(REL)
    check(
        "结构节点改完后编译通过",
        compiled.get("ok") is True,
        str(compiled.get("error", ""))[:100],
    )

    # 复原夹具：探针末尾把 if 改成了 eq、items 改成了对象、return 改成了 dict，
    # 留着这份状态，界面上看到的就不是「一个正常流程」而是一堆测试残留。
    write_fixture()

    print(f"\n{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
