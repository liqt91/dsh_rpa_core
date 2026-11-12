"""负向验证：拖拽落点 / 移动排序 / 前端接线两层判据是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，跑一遍相关单测，
断言 **实际失败集 == 按推理写下的期望失败集**（不是 >=，也不是「有红就行」）。
末尾两条是元验证：把判据放宽 / 摘掉后，同一处注入必须变绿，证明报红的是判据本身。
跑完逐字节还原所有目标文件并核 md5；任何异常都在 finally 里还原。

跑法：./.venv/Scripts/python.exe .harness/spike/negative_drag_drop.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

API = ".harness/demo/ppx_probe/app/api/api.py"
JS = ".harness/demo/ppx_probe/app/gui/src/main.js"
DIST_DIR = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist" / "assets"

T_DND = "tests/test_editor_drag_drop.py"
T_BR = "tests/test_ppx_bridge.py"
# 前端判据单跑会 0 passed，被「环境级非零」判据误杀 ⇒ 带一条必绿陪跑。
COMPANION = f"{T_BR}::test_dispatch_envelope_and_method_not_found"
FRONT = [f"{T_DND}::test_editor_wires_html5_drag_and_drop", COMPANION]
SEMANTIC = [T_DND]

W = f"{T_DND}::test_editor_wires_html5_drag_and_drop"
D1 = f"{T_DND}::test_drop_insert_uses_explicit_index_and_preserves_click_path"
D2 = f"{T_DND}::test_open_flow_cards_carry_ast_drop_locations"
D3 = f"{T_DND}::test_move_node_reorders_within_branch_and_across_branches"
D4 = f"{T_DND}::test_move_node_rejects_cycles_and_invalid_targets_without_writing"
D6 = f"{T_DND}::test_move_node_obeys_flow_changed_lock"


def case(
    cid: str,
    desc: str,
    rel: str,
    old: str,
    new: str,
    pytest: list[str],
    expect: set[str],
    *,
    extra: tuple[tuple[str, str, str], ...] = (),
    green: bool = False,
) -> dict[str, Any]:
    """green=True 是元验证：判据被放宽后，同一处注入应当不再报红。"""
    return {
        "id": cid,
        "desc": desc,
        "rel": rel,
        "old": old,
        "new": new,
        "pytest": pytest,
        "expect": expect,
        "extra": list(extra),
        "green": green,
    }


CASES = [
    # ---- 前端接线层：每条只打掉一处，期望只红静态接线判据 ----
    case(
        "F1", "指令拖动的 MIME 变了，drop 端读不到 payload", JS,
        "const DND_MIME = 'application/x-ppx-node'",
        "const DND_MIME = 'application/x-other'", FRONT, {W},
    ),
    case(
        "F2", "catalog 指令行不再可拖", JS,
        "      row.title = c.id\n      row.draggable = true\n",
        "      row.title = c.id\n      row.draggable = false\n", FRONT, {W},
    ),
    case(
        "F3", "移动改调一个不存在的 RPC", JS,
        "    const res = await ppx.call('rpa.moveNode', {",
        "    const res = await ppx.call('rpa.noSuchRpc', {", FRONT, {W},
    ),
    case(
        "F4", "目标行上/下半区换算反过来", JS,
        "(card.index ?? 0) + (before ? 0 : 1)",
        "(card.index ?? 0) + (before ? 1 : 0)", FRONT, {W},
    ),
    case(
        "F5", "拖入不再声明 explicit 落点", JS,
        "      args.explicit = true\n",
        "      args.explicit = false\n", FRONT, {W},
    ),
    case(
        "F6", "拖出画布后落点指示不再清理", JS,
        "document.addEventListener('dragend', clearDropHint)",
        "document.addEventListener('dragend', () => {})", FRONT, {W},
    ),
    # ---- 后端语义层 ----
    case(
        "B1", "_insert_at 忽略 index，一律追加末尾", API,
        "        bucket.insert(index, node)\n",
        "        bucket.append(node)\n", SEMANTIC, {D1, D3},
    ),
    case(
        "B2", "防环失效，容器可以拖进自己的分支", API,
        '    if (container.get("id") or "root") in subtree:\n',
        '    if False and (container.get("id") or "root") in subtree:\n',
        SEMANTIC, {D4},
    ),
    case(
        "B3", "同分支 pop 后不再修正下标", API,
        "    if container is src_parent and target_branch == src_branch and index > src_index:\n",
        "    if False and container is src_parent and target_branch == src_branch and index > src_index:\n",
        SEMANTIC, {D3},
    ),
    case(
        "B4", "卡片不再带真实 AST 下标", API,
        '        card.setdefault("index", index)\n',
        '        card.setdefault("index", 0)\n', SEMANTIC, {D2},
    ),
    case(
        "B5", "if 放开 children 这个非法分支", API,
        '    "if": ("then", "else"),\n',
        '    "if": ("then", "else", "children"),\n', SEMANTIC, {D4},
    ),
    case(
        "B6", "move 的乐观锁失效，别处改过盘仍继续写", API,
        "    # move 的乐观锁：与参数保存共用 _flows 里的 sha，挡住「别处改过盘还继续写」。\n"
        "    entry = _flows.get(path)\n"
        '    if entry is not None and entry["sha"] != _sha256(raw):\n',
        "    # move 的乐观锁：与参数保存共用 _flows 里的 sha，挡住「别处改过盘还继续写」。\n"
        "    entry = _flows.get(path)\n"
        '    if False and entry is not None and entry["sha"] != _sha256(raw):\n',
        SEMANTIC, {D6},
    ),
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_pytest(args: list[str]) -> tuple[set[str], str, int]:
    try:
        done = subprocess.run(
            # pyproject 的 addopts 已有 -q；再传一个会变 -qq，汇总行就没了。
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
            # Windows 上 pytest 回显 tests\xxx.py，nodeid 统一正斜杠
            failed.add(hit.group(1).replace("\\", "/"))
    return failed, out, done.returncode


def main() -> int:
    # 产物层判据要单独证明能红：注入 dist（gitignore 产物，md5 照样还原）。
    dist = next(
        (p for p in sorted(DIST_DIR.glob("*.js")) if "rpa.moveNode" in p.read_text(encoding="utf-8")),
        None,
    )
    if dist is None:
        print("MISSING dist/assets/*.js：先 npm run build 再跑负向验证")
        return 2
    f2 = next(c for c in CASES if c["id"] == "F2")
    test_rel = "apps/xhs-workbench/tests/test_editor_drag_drop.py"
    dist_rel = dist.relative_to(ROOT).as_posix()
    cases = [
        *CASES,
        case(
            "F7", "构建产物里没有新 RPC（改了 src 忘了 build）",
            dist_rel, "rpa.moveNode", "rpa.noSuchRpc", FRONT, {W},
        ),
        # 元验证：判据放宽 / 摘掉后，同一处注入必须变绿 ⇒ 报红的确实是这条判据。
        case(
            "M1", "等值断言放宽成 >= 后，指令行不可拖不再报红（元验证）",
            test_rel,
            '    assert src.count("row.draggable = true") == 2  # catalog 分组 + 流程控制分组',
            '    assert src.count("row.draggable = true") >= 1  # catalog 分组 + 流程控制分组',
            FRONT, set(),
            extra=((JS, f2["old"], f2["new"]),), green=True,
        ),
        case(
            "M2", "摘掉产物层判据后，dist 缺新 RPC 不再报红（元验证）",
            test_rel,
            '        assert "rpa.moveNode" in built',
            '        assert True  # 产物层判据被摘掉',
            FRONT, set(),
            extra=((dist_rel, "rpa.moveNode", "rpa.noSuchRpc"),), green=True,
        ),
    ]

    injections = [(c["rel"], c["old"], c["new"]) for c in cases]
    for c in cases:
        injections.extend(c["extra"])
    paths = {rel: ROOT / rel for rel, _, _ in injections}
    for rel, path in paths.items():
        if not path.is_file():
            print(f"MISSING {rel}")
            return 2
    originals = {rel: path.read_bytes() for rel, path in paths.items()}
    digests = {rel: md5(data) for rel, data in originals.items()}

    # 哨兵自检 + 锚点唯一性自检：按文件原文数，不按「我记得改了几处」。
    for rel, old, new in injections:
        text = originals[rel].decode("utf-8")
        if text.count(new) > 0:
            print(f"SENTINEL: {rel} 里残留注入态，先还原再跑")
            return 2
        if text.count(old) != 1:
            print(f"ANCHOR: {rel} 的 old 命中 {text.count(old)} 次，须恰好 1 次")
            return 2

    bad = 0
    try:
        for c in cases:
            todo = [(c["rel"], c["old"], c["new"]), *c["extra"]]
            texts = {rel: originals[rel].decode("utf-8") for rel, _, _ in todo}
            try:
                for rel, old, new in todo:
                    (ROOT / rel).write_bytes(texts[rel].replace(old, new, 1).encode("utf-8"))
                failed, out, rc = run_pytest(c["pytest"])
                has_passed = re.search(r"[1-9]\d* passed", out)
                if c["green"]:
                    ok = rc == 0 and failed == set() and has_passed is not None
                    print(f"[{c['id']}] {'GREEN-OK' if ok else 'BAD'} {c['desc']}")
                    print(
                        f"      rc={rc} failed={len(failed)} "
                        f"passed={has_passed.group(0) if has_passed else None}"
                    )
                else:
                    # 环境级非零（解释器崩 / 删除守卫）不算「判据红了」
                    counts = re.search(r"(\d+) failed", out)
                    has_error = re.search(r"\d+ error", out)
                    clean = (
                        rc != 0
                        and counts is not None
                        and int(counts.group(1)) == len(failed)
                        and has_error is None
                        and has_passed is not None
                    )
                    ok = clean and failed == c["expect"]
                    print(f"[{c['id']}] {'RED-OK' if ok else 'BAD'} {c['desc']}")
                    print(
                        f"      rc={rc} clean={clean} counts={counts.group(1) if counts else None} "
                        f"error={has_error.group(0) if has_error else None} "
                        f"passed={has_passed.group(0) if has_passed else None}"
                    )
                    print(f"      expect={sorted(c['expect'])}")
                    print(f"      actual={sorted(failed)}")
                    if not clean:
                        print(f"      tail={out.strip()[-400:]}")
                if not ok:
                    bad += 1
            finally:
                for rel in texts:
                    (ROOT / rel).write_bytes(originals[rel])
    finally:
        for rel, path in paths.items():
            path.write_bytes(originals[rel])
        for rel, path in paths.items():
            if md5(path.read_bytes()) != digests[rel]:
                print(f"RESTORE FAILED: {rel} 与基线 md5 不一致")
                bad += 1

    print(f"\nNEGATIVE VERIFY {'PASSED' if bad == 0 else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
