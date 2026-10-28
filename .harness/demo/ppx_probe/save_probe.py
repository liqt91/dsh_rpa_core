"""S5/S6 保存闭环探针：headless 直调 RPC，把「保存」的每一条承诺逐项打勾。

不靠观感。S6 起参数面板改成「全量提交、空 = 未设置」（与 Qt
`holder.raw["with"] = dict(values)` 同语义），判据随之改口径：

字段表
1. 声明了但节点里没写的参数**也出现在字段表里**（`set=False`）——这是 S6 的核心，
   只渲染已有键等于让「可以设但还没设」的参数量没有入口
2. 类型契约：manifest 声明优先；未声明按既有值推断；两者都无回落 string
3. `type` 为**列表**时取第一个非 null（`data.setVar.value` 是 7 个候选的联合）
4. `x-param-groups` 分组顺序 + 折叠标记；未声明键归「未声明」组
5. `x-hidden` / `hidden` 字段不给入口（`flowDir` 由运行期注入）

保存
6. 改三个参数 → ok、changed=True、编译通过
7. 落盘后只有被改的那几行变化（未触碰的字节不许动）
8. 同值再存 → changed=False、文件 SHA 不变
9. 类型不符（"abc" 给 integer）→ INVALID_VALUE、文件 SHA 不变
10. JSON 合法但类型不符（"\"abc\""）→ 同样拒（不许静默转字符串）
11. **漏传一个键 → INCOMPLETE_SUBMIT**，文件不动（全量提交的防呆：漏传等于悄悄删参）
12. 传一个既不在声明也不在现有 with 里的键 → INVALID_VALUE
13. 空文本 → 该键从 `with` 里删除（`removed` 报出来）
14. 未声明的键改成空文本 → 同样删除，但改成同类型新值 → 接受
15. 非 action 节点 → NODE_NOT_EDITABLE
16. 外部改过文件 → FLOW_CHANGED，且磁盘保持外部内容
17. **`_` 前缀的私有键不受影响**（`_exprModes` 之类的编辑器自留地）
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
SANDBOX = HERE / "_sandbox"
SOURCE = REPO / "examples" / "baidu-news-top10" / "workflow.json"
TARGET = SANDBOX / "demo.json"
REL = TARGET.relative_to(REPO).as_posix()

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def sha() -> str:
    return hashlib.sha256(TARGET.read_bytes()).hexdigest()


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


def main() -> int:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)

    sys.path.insert(0, str(HERE / "app"))
    import api.api as A  # noqa: E402 - 必须在 sys.path 之后导入

    info = A.bootstrap()
    paths = [f["path"] for f in info["flows"]]
    check("sandbox 出现在流程列表里", REL in paths, f"{len(paths)} 个流程")

    flow = A.open_flow(REL)
    cards = [c for c in flow["cards"] if c["kind"] == "action"]
    first = next(c for c in cards if c["id"] == "openHome")

    detail = A.node_detail(REL, first["id"])
    by_key = {f["key"]: f for f in detail["fields"]}
    on_disk = disk_node(first["id"])["with"]
    print(f"       ↳ 节点 with 里实际有：{list(on_disk)}")
    print(f"       ↳ 字段表里有：{list(by_key)}")

    # ---- 1. 声明了但没写的参数也在字段表里 ---------------------------------
    declared = set(by_key)
    unset = sorted(f["key"] for f in detail["fields"] if not f["set"])
    check(
        "声明了但未设置的参数也给出入口（set=False）",
        len(unset) >= 1 and "browserType" in by_key and by_key["browserType"]["set"] is False,
        f"未设置：{unset}",
    )
    check(
        "字段表覆盖 with 里的全部键（不丢既有内容）",
        set(on_disk) <= declared,
        f"缺：{sorted(set(on_disk) - declared)}",
    )

    # ---- 2. 类型契约 ------------------------------------------------------
    check(
        "类型契约：声明优先 / 未声明按既有值推断",
        by_key["url"]["type"] == "string"
        and by_key["url"]["typeSource"] == "manifest"
        and by_key["timeoutMs"]["type"] == "integer"
        and by_key["headless"]["type"] == "boolean"
        and by_key["headless"]["typeSource"] == "existing",
        f"url={by_key['url']['type']}/{by_key['url']['typeSource']} "
        f"headless={by_key['headless']['type']}/{by_key['headless']['typeSource']}",
    )

    # ---- 3. type 是列表（联合类型）----------------------------------------
    # 直接喂假节点调 `_field_descriptors`：靠「流程里恰好有 setVar 节点」会让这条判据
    # 在某些样例上静默跳过——跳过 = 没验过（Qt 侧 `_effective_type` 同口径）。
    union_node = {"type": "action", "with": {"name": "x", "value": 1}}
    union = {f["key"]: f for f in A._field_descriptors("data.setVar", union_node)["fields"]}
    check(
        "type 为列表（联合类型）时取第一个非 null，不是 str(list)",
        union["value"]["type"] == "string" and union["value"]["typeSource"] == "manifest",
        f"value.type={union['value']['type']!r}",
    )

    # ---- 4 / 5. 分组与 hidden ---------------------------------------------
    labels = [g["label"] for g in detail["groups"]]
    check(
        "分组顺序取自 x-param-groups，未声明键归「未声明」",
        labels == ["常规", "高级", "未声明"]
        and next(g for g in detail["groups"] if g["label"] == "高级")["collapsed"] is True,
        f"{labels}",
    )
    # hidden 判据要**直接喂一个含 hidden 字段的节点**，不能拿 browser.navigate 的字段表
    # 去断言「flowDir 不在里面」——它本来就不是 navigate 的参数，那种断言恒绿。
    # ★ 注意 `varName` 只对 `data.table.getCell` 声明为 hidden，`setCell` 没有——所以
    # 它在这里属于**合法的未声明参数**，该出现在「未声明」组，断言不能把它一起排除。
    probe_node = {"type": "action", "with": {"row": 1, "column": "A", "value": "x",
                                             "flowDir": "/tmp"}}
    probed = A._field_descriptors("data.table.setCell", probe_node)["fields"]
    probed_keys = {f["key"] for f in probed}
    check(
        "x-hidden 字段不给入口（flowDir 由运行期注入）",
        "flowDir" not in probed_keys and {"row", "column", "value"} <= probed_keys,
        f"字段={sorted(probed_keys)}",
    )
    # 反证：hidden 字段在 with 里存在时，既不该渲染、也不该要求提交，
    # 但它的值必须原样留住（否则任何 data.table.* 节点都保存不了）。
    # ★ 造一份真文件走端到端，"样例里恰好没有这种节点就跳过" = 没验过。
    # 放子目录：`bootstrap()` 只扫 `_sandbox/*.json`，这份探针夹具不该出现在流程下拉框里
    (SANDBOX / "_probe").mkdir(parents=True, exist_ok=True)
    hiddenflow = SANDBOX / "_probe" / "hidden.json"
    hiddenflow.write_bytes(
        (
            json.dumps(
                {
                    "schema_version": "1.0",
                    "id": "hidden-probe",
                    "name": "hidden field probe",
                    "inputs": {},
                    "root": {
                        "type": "sequence",
                        "id": "root",
                        "children": [
                            {
                                "type": "action",
                                "id": "setcell",
                                "command": "data.table.setCell",
                                "with": {
                                    "flowDir": "/tmp/probe",
                                    "table": "default",
                                    "row": 1,
                                    "column": "A",
                                    "value": "v",
                                },
                            }
                        ],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    )
    hidden_rel = hiddenflow.relative_to(REPO).as_posix()
    A.open_flow(hidden_rel)
    hidden_fields = {f["key"]: f["valueText"] for f in A.node_detail(hidden_rel, "setcell")["fields"]}
    check(
        "with 里的 hidden 字段（flowDir）不出现在表单里",
        "flowDir" not in hidden_fields,
        f"表单字段={sorted(hidden_fields)}",
    )
    hidden_saved = A.save_node_params(hidden_rel, "setcell", {**hidden_fields, "row": "7"})
    check(
        "带 hidden 字段的节点能正常保存（不判成漏参）",
        hidden_saved.get("ok") is True,
        f"{hidden_saved.get('code')} {str(hidden_saved.get('error'))[:60]}",
    )
    # 不删这份临时流程：`unlink()` 会触发本仓的删除守卫（fail-closed），
    # 让整个探针以环境级非零收尾，掩盖真实判据。
    hidden_on_disk = json.loads(hiddenflow.read_text(encoding="utf-8"))["root"]["children"][0]["with"]
    check(
        "hidden 字段的值原样留在文件里，且 row 已改成 7",
        hidden_on_disk.get("flowDir") == "/tmp/probe" and hidden_on_disk.get("row") == 7,
        json.dumps(hidden_on_disk, ensure_ascii=False)[:90],
    )

    # ---- 6 / 7. 保存 -------------------------------------------------------
    before_text = TARGET.read_text(encoding="utf-8")
    sha_before = sha()
    full = {key: field["valueText"] for key, field in by_key.items()}
    full["url"] = "https://www.baidu.com/s?wd=PPX"
    full["timeoutMs"] = "30000"
    full["headless"] = "true"

    saved = A.save_node_params(REL, first["id"], full)
    check(
        "全量提交保存成功",
        saved.get("ok") is True,
        json.dumps({k: saved.get(k) for k in ("code", "error")}, ensure_ascii=False)[:160],
    )
    check("changed=True", saved.get("changed") is True, f"bytes {saved.get('bytes')}")
    check(
        "保存后编译通过",
        bool((saved.get("compile") or {}).get("ok")),
        str((saved.get("compile") or {}).get("error", ""))[:100],
    )
    node = disk_node(first["id"])
    check(
        "磁盘上确实是新值、且类型没变",
        node["with"]["url"].endswith("wd=PPX")
        and node["with"]["timeoutMs"] == 30000
        and node["with"]["headless"] is True,
        json.dumps(node["with"], ensure_ascii=False)[:100],
    )
    check(
        "未声明的键原样保留（没被全量替换冲掉）",
        "userAgent" in node["with"],
        f"with={list(node['with'])}",
    )

    after_text = TARGET.read_text(encoding="utf-8")
    diff = [
        line
        for line in difflib.unified_diff(
            before_text.splitlines(), after_text.splitlines(), lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    check(
        "未触碰的行逐字节不变（改 3 个参数只动 3 行）",
        len(diff) == 6,
        f"{len(diff)} 条 +/-：{[d.strip()[:44] for d in diff]}",
    )

    # ---- 8. 幂等 ----------------------------------------------------------
    sha_after = sha()
    again = A.save_node_params(REL, first["id"], full)
    check(
        "同值再存不写盘",
        again.get("ok") is True and again.get("changed") is False,
        f"bytes {again.get('bytes')}",
    )
    check("同值再存文件 SHA 不变", sha() == sha_after)

    # ---- 9 / 10. 类型拒收 -------------------------------------------------
    bad = A.save_node_params(REL, first["id"], {**full, "timeoutMs": "abc"})
    check(
        "类型不符被拒（INVALID_VALUE）",
        bad.get("ok") is False and bad.get("code") == "INVALID_VALUE",
        str(bad.get("error", ""))[:70],
    )
    quoted = A.save_node_params(REL, first["id"], {**full, "timeoutMs": '"abc"'})
    check(
        "JSON 合法但类型不符也被拒（不静默转字符串）",
        quoted.get("ok") is False and quoted.get("code") == "INVALID_VALUE",
        str(quoted.get("error", ""))[:70],
    )

    # ---- 11. 漏传键 -------------------------------------------------------
    partial = {k: v for k, v in full.items() if k != "timeoutMs"}
    incomplete = A.save_node_params(REL, first["id"], partial)
    check(
        "漏传键 → INCOMPLETE_SUBMIT（不按未设置猜）",
        incomplete.get("ok") is False and incomplete.get("code") == "INCOMPLETE_SUBMIT",
        str(incomplete.get("error", ""))[:80],
    )
    check("三次拒绝都没动文件", sha() == sha_after)

    # ---- 12. 凭空多出来的键 -----------------------------------------------
    ghost = A.save_node_params(REL, first["id"], {**full, "notInSchema": "x"})
    check(
        "既不在声明也不在 with 里的键被拒",
        ghost.get("ok") is False and ghost.get("code") == "INVALID_VALUE",
        str(ghost.get("error", ""))[:80],
    )

    # ---- 13 / 14. 空文本 = 删除 -------------------------------------------
    cleared = A.save_node_params(REL, first["id"], {**full, "userAgent": ""})
    check(
        "空文本 → 键从 with 里删除",
        cleared.get("ok") is True and cleared.get("removed") == ["userAgent"],
        f"removed={cleared.get('removed')}",
    )
    check("删除后磁盘上确实没了", "userAgent" not in disk_node(first["id"])["with"])
    full.pop("userAgent", None)

    # ---- 15. 非 action 节点 -----------------------------------------------
    structural = A.save_node_params(REL, "root", {"x": "1"})
    check(
        "非 action 节点不可编辑",
        structural.get("ok") is False and structural.get("code") == "NODE_NOT_EDITABLE",
        str(structural.get("error", ""))[:60],
    )

    # ---- 17. 私有键不受影响 -----------------------------------------------
    live = json.loads(TARGET.read_text(encoding="utf-8"))
    for child in live["root"]["children"]:
        if child.get("id") == first["id"]:
            child.setdefault("with", {})["_exprModes"] = {"url": "python"}
    TARGET.write_bytes((json.dumps(live, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    A.open_flow(REL)
    detail2 = A.node_detail(REL, first["id"])
    keys2 = {f["key"]: f["valueText"] for f in detail2["fields"]}
    check("私有键不进字段表", "_exprModes" not in keys2, str(sorted(keys2)))
    saved2 = A.save_node_params(REL, first["id"], keys2)
    check(
        "保存后私有键原样留住",
        saved2.get("ok") is True and disk_node(first["id"])["with"].get("_exprModes") == {"url": "python"},
        str(disk_node(first["id"])["with"].get("_exprModes")),
    )

    # ---- 16. 外部改文件 ---------------------------------------------------
    external = json.loads(TARGET.read_text(encoding="utf-8"))
    for child in external["root"]["children"]:
        if child.get("id") == first["id"]:
            child["with"]["userAgent"] = "external-edit"
    TARGET.write_bytes((json.dumps(external, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sha_external = sha()
    stale = A.save_node_params(REL, first["id"], keys2)
    check(
        "外部改过 → FLOW_CHANGED",
        stale.get("ok") is False and stale.get("code") == "FLOW_CHANGED",
        str(stale.get("error", ""))[:60],
    )
    check("FLOW_CHANGED 时不覆盖外部内容", sha() == sha_external)

    # 复原成干净样例：探针最后一步故意造了「外部编辑」现场，留着会让界面里
    # 显示 userAgent=external-edit，下次谁看到都以为有问题。
    shutil.copyfile(SOURCE, TARGET)

    print(f"\n{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
