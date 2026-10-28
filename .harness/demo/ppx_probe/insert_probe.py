"""S6c 插入 / 删除节点探针。

落点语义是最容易做错的部分：Qt 的规则是「插到 anchor 所属**分支**的末尾」，
不是「紧接 anchor 之后」。判据必须能区分这两者——所以夹具在每个分支里都放
两个以上节点，锚点取**第一个**：如果实现成「紧跟其后」，断言会立刻红。

夹具结构：
    root(sequence)
      ├ a1  a2                     ← 顶层两个，用来验「锚点不是插入点」
      ├ if1                        ← then 里 t1/t2、else 里 e1
      ├ fe1(forEach) children: l1
      └ try1         children: y1

判据：
落点
 1. 空锚点 → root.children 末尾（不是开头）
 2. 锚点是叶子 a1 → root.children **末尾**（不是 a1 之后）
 3. 锚点是 if1（容器）→ if1.then 末尾
 4. 锚点是 then 里的叶子 t1 → if1.then 末尾（沿用父容器那条分支）
 5. 锚点是 else 里的叶子 e1 → if1.**else** 末尾（不能串到 then）
 6. 锚点是 forEach → 它自己的 children 末尾
 7. 新节点 id 不与现有冲突、`with` 为空（与 Qt `create_action_item` 一致）
 8. 插入后编译通过（编译期不校验 required，所以空 with 是能过的）

拒收
 9. 目录里没有的命令 → UNKNOWN_COMMAND
10. 不存在的锚点 → INVALID_ANCHOR
11. 外部改过文件 → FLOW_CHANGED，不覆盖

删除
12. 删叶子 a2 → 文件里没了，其它节点原样
13. 删 if1 → 它的 then/else 里 3 个节点一起没（`removed.ids` 报全）
14. 删 root → ROOT_NOT_DELETABLE
15. 删不存在的 id → NODE_NOT_FOUND
16. 删除后编译通过
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
SANDBOX = HERE / "_sandbox"
TARGET = SANDBOX / "insert.json"
REL = TARGET.relative_to(REPO).as_posix()


def log(node_id: str, message: str) -> dict:
    return {"type": "action", "id": node_id, "command": "data.log", "with": {"message": message}}


FIXTURE = {
    "schema_version": "1.0",
    "id": "insert-probe",
    "name": "insert / delete probe",
    "inputs": {"flag": True},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            log("a1", "one"),
            log("a2", "two"),
            {
                "type": "if",
                "id": "if1",
                "condition": {"op": "truthy", "left": "${inputs.flag}"},
                "then": [log("t1", "then-1"), log("t2", "then-2")],
                "else": [log("e1", "else-1")],
            },
            {
                "type": "forEach",
                "id": "fe1",
                "items": [],
                "item_var": "row",
                "children": [log("l1", "loop")],
            },
            {
                "type": "try",
                "id": "try1",
                "error_var": "err",
                "children": [log("y1", "try")],
                "catch": [],
            },
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
    SANDBOX.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes((json.dumps(FIXTURE, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def doc() -> dict:
    return json.loads(TARGET.read_text(encoding="utf-8"))


def ids_in(node: dict) -> list[str]:
    out = [node.get("id", "")]
    for key in ("children", "then", "else", "catch"):
        for child in node.get(key) or []:
            out.extend(ids_in(child))
    return out


def main() -> int:
    write_fixture()
    sys.path.insert(0, str(HERE / "app"))
    import api.api as A  # noqa: E402

    A.open_flow(REL)

    def last_child_of(node_id: str, branch: str) -> str | None:
        """看磁盘上某个容器某条分支的末位子节点 id。"""
        root = doc()["root"]
        stack = [root]
        while stack:
            node = stack.pop()
            if node.get("id") == node_id:
                bucket = node.get(branch) or []
                return bucket[-1]["id"] if bucket else None
            for key in ("children", "then", "else", "catch"):
                stack.extend(node.get(key) or [])
        return None

    # ---- 1. 空锚点 → root 末尾 -------------------------------------------
    res = A.insert_node(REL, "data.log", "")
    check(
        "空锚点插到 root.children 末尾（不是开头）",
        res.get("ok") is True and res["placed"]["parent"] == "root"
        and res["placed"]["index"] == len(FIXTURE["root"]["children"]),
        f"{res.get('code')} {res.get('placed')}",
    )
    first_new = res.get("newNodeId")

    # ---- 2. 锚点是叶子 a1 → root 末尾（不是 a1 之后）----------------------
    res = A.insert_node(REL, "data.log", "a1")
    root_ids = [c.get("id") for c in doc()["root"]["children"]]
    check(
        "锚点是叶子 → 插到父容器末尾，不是紧跟其后",
        res.get("ok") is True and res["placed"]["parent"] == "root"
        and res["placed"]["index"] == len(root_ids) - 1 and root_ids[-1] == res["newNodeId"],
        f"placed={res.get('placed')} root={root_ids}",
    )

    # ---- 3. 锚点是 if1 → then 末尾 ---------------------------------------
    res = A.insert_node(REL, "data.log", "if1")
    check(
        "锚点是 if 容器 → 插到 then 末尾",
        res.get("ok") is True and res["placed"]["branch"] == "then"
        and last_child_of("if1", "then") == res["newNodeId"],
        f"placed={res.get('placed')}",
    )
    then_len = len([c for c in doc()["root"]["children"] if c.get("id") == "if1"][0]["then"])

    # ---- 4. 锚点是 then 里的叶子 t1 → then 末尾 --------------------------
    res = A.insert_node(REL, "data.log", "t1")
    then_after = len([c for c in doc()["root"]["children"] if c.get("id") == "if1"][0]["then"])
    check(
        "锚点是 then 里的叶子 → 仍在 then 末尾",
        res.get("ok") is True and res["placed"]["branch"] == "then"
        and last_child_of("if1", "then") == res["newNodeId"] and then_after == then_len + 1,
        f"then {then_len}→{then_after}",
    )

    # ---- 5. 锚点是 else 里的叶子 e1 → else 末尾（不串到 then）------------
    res = A.insert_node(REL, "data.log", "e1")
    check(
        "锚点是 else 里的叶子 → 插到 else 末尾，不串到 then",
        res.get("ok") is True and res["placed"]["branch"] == "else"
        and last_child_of("if1", "else") == res["newNodeId"],
        f"placed={res.get('placed')}",
    )

    # ---- 6. 锚点是 forEach → 它自己的 children ---------------------------
    res = A.insert_node(REL, "data.log", "fe1")
    check(
        "锚点是 forEach → 插到它自己的 children 末尾",
        res.get("ok") is True and res["placed"]["parent"] == "fe1"
        and last_child_of("fe1", "children") == res["newNodeId"],
        f"placed={res.get('placed')}",
    )

    # ---- 7. id 不冲突、with 为空 -----------------------------------------
    all_ids = ids_in(doc()["root"])
    check(
        "新 id 不与现有 id 冲突",
        len(all_ids) == len(set(all_ids)),
        f"{len(all_ids)} 个 id，去重后 {len(set(all_ids))}",
    )
    new_nodes = [c for c in doc()["root"]["children"] if c.get("id") == first_new]
    check(
        "新节点的 with 为空（与 Qt create_action_item 一致）",
        bool(new_nodes) and new_nodes[0].get("with") == {},
        json.dumps(new_nodes[0].get("with") if new_nodes else None),
    )

    # ---- 8. 插入后编译通过 -----------------------------------------------
    compiled = A.compile_check(REL)
    check(
        "插入空参数节点后编译通过（编译期不校验 required）",
        compiled.get("ok") is True,
        str(compiled.get("error", ""))[:100],
    )

    # ---- 9 / 10. 拒收 ----------------------------------------------------
    sha_now = sha()
    unknown = A.insert_node(REL, "no.such.command", "")
    check(
        "目录里没有的命令 → UNKNOWN_COMMAND",
        unknown.get("ok") is False and unknown.get("code") == "UNKNOWN_COMMAND",
        str(unknown.get("error", ""))[:70],
    )
    ghost = A.insert_node(REL, "data.log", "noSuchNode")
    check(
        "不存在的锚点 → INVALID_ANCHOR",
        ghost.get("ok") is False and ghost.get("code") == "INVALID_ANCHOR",
        str(ghost.get("error", ""))[:70],
    )
    check("两次拒收都没动文件", sha() == sha_now)

    # ---- 12. 删叶子 ------------------------------------------------------
    removed = A.delete_node(REL, "a2")
    check(
        "删叶子 a2 成功",
        removed.get("ok") is True and removed["removed"]["ids"] == ["a2"],
        f"{removed.get('code')} {removed.get('removed')}",
    )
    check("文件里确实没有了 a2", "a2" not in ids_in(doc()["root"]))
    check(
        "其它节点原样（a1 / if1 / fe1 / try1 都还在）",
        {"a1", "if1", "fe1", "try1"} <= set(ids_in(doc()["root"])),
        f"{sorted(ids_in(doc()['root']))}",
    )

    # ---- 13. 删容器（子树一起没）----------------------------------------
    subtree = A.delete_node(REL, "if1")
    check(
        "删 if1 → 连同 then/else 的节点一起没了",
        subtree.get("ok") is True
        and set(subtree["removed"]["ids"]) >= {"if1", "t1", "t2", "e1"},
        f"{subtree.get('removed')}",
    )
    remaining = set(ids_in(doc()["root"]))
    check(
        "被删子树里的节点在文件里一个不剩",
        not ({"if1", "t1", "t2", "e1"} & remaining),
        f"{sorted(remaining)}",
    )

    # ---- 14 / 15. 删的拒收 -----------------------------------------------
    root_del = A.delete_node(REL, "root")
    check(
        "删根节点被拒（ROOT_NOT_DELETABLE）",
        root_del.get("ok") is False and root_del.get("code") == "ROOT_NOT_DELETABLE",
        str(root_del.get("error", ""))[:60],
    )
    missing = A.delete_node(REL, "noSuchNode")
    check(
        "删不存在的节点 → NODE_NOT_FOUND",
        missing.get("ok") is False and missing.get("code") == "NODE_NOT_FOUND",
        str(missing.get("error", ""))[:60],
    )

    # ---- 16. 删除后编译通过 ----------------------------------------------
    check(
        "删除后编译通过",
        A.compile_check(REL).get("ok") is True,
        str(A.compile_check(REL).get("error", ""))[:100],
    )

    # ---- 11. SHA 闸门（放最后：会改文件）---------------------------------
    external = doc()
    external["root"]["children"].append(log("ext1", "external"))
    TARGET.write_bytes((json.dumps(external, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sha_external = sha()
    stale = A.insert_node(REL, "data.log", "")
    check(
        "外部改过文件 → 插入被 FLOW_CHANGED 拒绝",
        stale.get("ok") is False and stale.get("code") == "FLOW_CHANGED",
        str(stale.get("error", ""))[:60],
    )
    stale_del = A.delete_node(REL, "a1")
    check(
        "外部改过文件 → 删除也被 FLOW_CHANGED 拒绝",
        stale_del.get("ok") is False and stale_del.get("code") == "FLOW_CHANGED",
        str(stale_del.get("error", ""))[:60],
    )
    check("FLOW_CHANGED 时不覆盖外部内容", sha() == sha_external)

    # 复原夹具，别把测试残留留在界面上
    write_fixture()

    print(f"\n{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
