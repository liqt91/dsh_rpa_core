"""负向验证：新增的 check_xhs_products_view.mjs 判据是否真会红。

两个方向都做（改「读侧」前端 / 改「写侧」后端导出），断言
**实际失败集 == 按推理写下的期望失败集**，末尾逐字节还原并核 sha256。
跑法：./.venv/Scripts/python.exe .harness/spike/negative_xhs_products_view.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node") or "node"
GATE = ROOT / "scripts/check_xhs_products_view.mjs"
HTML = ROOT / "apps/xhs-workbench/static/index.html"
PY = ROOT / "apps/xhs-workbench/src/snapshots.py"
SENTINEL = "NV-M70-VIEW"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate() -> set[str]:
    done = subprocess.run(
        [NODE, str(GATE)], cwd=str(ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    failed: set[str] = set()
    for line in (done.stdout or "").splitlines():
        hit = re.match(r"^FAIL (.+?)(?: — .*)?$", line.strip())
        if hit:
            failed.add(hit.group(1).strip())
    return failed


def replace_once(path: Path, old: str, new: str) -> bytes:
    original = path.read_bytes()
    text = original.decode("utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path.name} 锚点命中 {count} 次，拒绝注入")
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    return original


VIEW_INTERACTION = "互动数据三列：赞 / 藏 / 评 都要出现在渲染结果里"
VIEW_COMMENTS = "逐条评论：条数、作者、正文都要在展开区里"
VIEW_RECONCILE = "双向对账：Python 导出的每个字段前端都消费，前端用的每个字段后端都导出"
VIEW_META = "元验证：字段名漂移时判据本身要能报红"
VIEW_LEGACY = "老数据兼容：字段缺失不出现 undefined / NaN，也不硬塞空展开区"

CASES = [
    dict(
        id="V1",
        desc="读侧：前端把 it.likes 写成 it.likeCount（字段名漂移）",
        target=HTML,
        old="it.likes && `赞 ${it.likes}`",
        new="it.likeCount && `赞 ${it.likeCount}`",
        # 推理：渲染里没有「赞 1.2万」→ 互动数据红；前端键集合少 likes、多
        # likeCount → 双向对账红；元验证本身是拿 fnSrc 再做一次漂移注入，
        # 漂移后 fnSrc 里已无 it.likes 可替换 ⇒ 注入不生效 ⇒ 它也红。
        expect={VIEW_INTERACTION, VIEW_RECONCILE, VIEW_META},
    ),
    dict(
        id="V2",
        desc="读侧：前端不再渲染逐条评论（只留评论数）",
        target=HTML,
        old="const comments = Array.isArray(it.commentList) ? it.commentList : [];",
        new=f"const comments = []; /* {SENTINEL} */",
        # 推理：评论展开区消失 → 逐条评论红；前端仍引用 it.commentList 吗？
        # 这行被替换后 fnSrc 里再无 it.commentList ⇒ 对账发现后端多导出一项 → 红。
        expect={VIEW_COMMENTS, VIEW_RECONCILE},
    ),
    dict(
        id="V3",
        desc="写侧：后端 to_json 不再导出 collects / comments",
        target=PY,
        old='            "collects": self.collects,\n            "comments": self.comments,',
        new=f"            # {SENTINEL}\n",
        # 推理：Python 导出键少两项而前端仍消费 ⇒ 双向对账红；
        # 元验证拿 pythonKeys 与漂移键比对，少键不影响它的结论 ⇒ 不红。
        # 渲染层用的是 payload 假数据，不读 Python ⇒ 其余判据不红。
        expect={VIEW_RECONCILE},
    ),
]


def main() -> int:
    files = (HTML, PY, GATE)
    if any(SENTINEL in p.read_text(encoding="utf-8") for p in files):
        print("源码里已有哨兵，拒绝运行（上次注入没还原干净）")
        return 2
    originals = {p: p.read_bytes() for p in files}
    before = {p: digest(p) for p in files}
    print("基线:", {p.name: v[:8] for p, v in before.items()})

    baseline = run_gate()
    if baseline:
        print(f"FAIL 基线自身不绿：{sorted(baseline)}")
        return 1

    failures = 0
    for item in CASES:
        problems: list[str] = []
        try:
            replace_once(item["target"], item["old"], item["new"])
            actual = run_gate()
            expected = set(item["expect"])
            if actual != expected:
                problems.append(f"  期望红={sorted(expected)}")
                problems.append(f"  实际红={sorted(actual)}")
                if expected - actual:
                    problems.append(f"  漏报（假绿灯）：{sorted(expected - actual)}")
                if actual - expected:
                    problems.append(f"  意外红：{sorted(actual - expected)}")
        except Exception as exc:  # noqa: BLE001 - 注入异常也算失败，finally 负责还原
            problems.append(f"  注入/执行异常：{exc}")
        finally:
            for path, data in originals.items():
                path.write_bytes(data)
        if problems:
            failures += 1
            print(f"FAIL {item['id']} {item['desc']}")
            for line in problems:
                print(line)
        else:
            print(f"GREEN (negative verified) {item['id']} {item['desc']}")

    after = {p: digest(p) for p in files}
    if before != after:
        failures += 1
        print("FAIL 还原不彻底：")
        for path, value in before.items():
            if after[path] != value:
                print(f"  {path.name} {value[:8]} -> {after[path][:8]}")
    for path in files:
        if SENTINEL in path.read_text(encoding="utf-8"):
            failures += 1
            print(f"FAIL {path.name} 里残留哨兵")
    total = len(CASES)
    print(f"NEGATIVE VERIFICATION {'FAILED' if failures else 'PASSED'} "
          f"({total - failures}/{total})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
