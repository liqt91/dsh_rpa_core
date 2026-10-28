"""S5 保存闭环探针：headless 直调 RPC，把「保存」的每一条承诺逐项打勾。

不靠观感。九条：
1. 字段表能对上 manifest 声明（url/string、timeoutMs/integer、headless/boolean）
2. 改三个参数 → ok、changed=True、编译通过
3. 落盘后只有被改的那几行变化（未触碰的字节不许动）
4. 同值再存 → changed=False、文件 SHA 不变（不做无谓写盘）
5. 类型不符（"abc" 给 integer）→ INVALID_VALUE、文件 SHA 不变
6. 类型对不上但 JSON 合法（"\\"abc\\""）→ 同样拒（不许静默转字符串）
7. 新增键 → 拒，理由指向 S6 不做
8. 非 action 节点 → NODE_NOT_EDITABLE
9. 外部改过文件 → FLOW_CHANGED，且磁盘保持外部内容（不覆盖别人的编辑）
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
    check(
        "字段类型：声明优先、未声明按既有值推断",
        by_key["url"]["type"] == "string"
        and by_key["url"]["typeSource"] == "manifest"
        and by_key["timeoutMs"]["type"] == "integer"
        and by_key["headless"]["type"] == "boolean"
        and by_key["headless"]["typeSource"] == "existing",
        f"{[(f['key'], f['type'], f['typeSource']) for f in detail['fields']]}",
    )
    undeclared = sorted(f["key"] for f in detail["fields"] if not f["declared"])
    print(f"       ↳ 该节点里 manifest 未声明的参数：{undeclared or '（无）'}")

    before_text = TARGET.read_text(encoding="utf-8")
    sha_before = sha()

    saved = A.save_node_params(
        REL,
        first["id"],
        {"url": "https://www.baidu.com/s?wd=PPX", "timeoutMs": "30000", "headless": "true"},
    )
    check("保存返回 ok", saved.get("ok") is True, json.dumps({k: saved.get(k) for k in ("code", "error")}, ensure_ascii=False))
    check("changed=True", saved.get("changed") is True, f"bytes {saved.get('bytes')}")
    check("保存后编译通过", bool((saved.get("compile") or {}).get("ok")), str((saved.get("compile") or {}).get("error", ""))[:80])

    on_disk = json.loads(TARGET.read_text(encoding="utf-8"))
    node = next(n for n in on_disk["root"]["children"] if n.get("id") == "openHome")
    check(
        "磁盘上确实是新值、且类型没变",
        node["with"]["url"].endswith("wd=PPX")
        and node["with"]["timeoutMs"] == 30000
        and node["with"]["headless"] is True,
        json.dumps(node["with"], ensure_ascii=False)[:80],
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

    sha_after = sha()
    again = A.save_node_params(
        REL,
        first["id"],
        {"url": "https://www.baidu.com/s?wd=PPX", "timeoutMs": "30000", "headless": "true"},
    )
    check("同值再存不写盘", again.get("ok") is True and again.get("changed") is False, f"bytes {again.get('bytes')}")
    check("同值再存文件 SHA 不变", sha() == sha_after)

    bad = A.save_node_params(REL, first["id"], {"timeoutMs": "abc"})
    check(
        "类型不符被拒（INVALID_VALUE）",
        bad.get("ok") is False and bad.get("code") == "INVALID_VALUE",
        str(bad.get("error", ""))[:70],
    )
    quoted = A.save_node_params(REL, first["id"], {"timeoutMs": '"abc"'})
    check(
        "JSON 合法但类型不符也被拒（不静默转字符串）",
        quoted.get("ok") is False and quoted.get("code") == "INVALID_VALUE",
        str(quoted.get("error", ""))[:70],
    )
    check("两次拒绝都没动文件", sha() == sha_after)

    extra = A.save_node_params(REL, first["id"], {"newParam": "x"})
    check(
        "新增键被拒并指向 S6",
        extra.get("ok") is False and extra.get("code") == "INVALID_VALUE",
        str(extra.get("error", ""))[:90],
    )

    structural = A.save_node_params(REL, "root", {"x": "1"})
    check(
        "非 action 节点不可编辑",
        structural.get("ok") is False and structural.get("code") == "NODE_NOT_EDITABLE",
        str(structural.get("error", ""))[:60],
    )

    # 外部改文件：模拟用户在 Qt 里存了同一份流程
    external = json.loads(TARGET.read_text(encoding="utf-8"))
    other = next(n for n in external["root"]["children"] if n.get("id") == "openHome")
    other["with"]["userAgent"] = "external-edit"
    TARGET.write_bytes((json.dumps(external, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sha_external = sha()
    stale = A.save_node_params(REL, first["id"], {"url": "https://example.com"})
    check(
        "外部改过 → FLOW_CHANGED",
        stale.get("ok") is False and stale.get("code") == "FLOW_CHANGED",
        str(stale.get("error", ""))[:60],
    )
    check("FLOW_CHANGED 时不覆盖外部内容", sha() == sha_external)

    print(f"\n{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
