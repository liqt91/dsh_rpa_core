"""负向验证：把元素资产 RPC 的三条判据各自打断一次，确认 probe_elements.py 真的会红。

不是「跑一遍探针绿了就放心」——绿的探针可能什么都没查。这里往 api.py 里精准注入
三处**合法语法的精准放宽**，期望**具体哪几条**判据变红（不多不少），再逐字节还原
并复核 md5。机制与 `probe_exec_rpc_negative.py` 同款。

注入一（待补永不报）：`if name not in present:` → `if False:`
    缺失的元素再也进不了 missing ⇒ 前端「⚠ 待补」整排消失，运行期才炸
    ELEMENT_REF_MISSING。期望红（恰好 2 条，同源连带）：
      - missingElements 命中待补元素 missing_button
      - 待补条目带 nodeId/command/param（前端定位卡片就靠它）
        ★ 第一轮负向验证补的：定位字段判据的 next() 是从同一个 missing 里取条目，
        missing 空了它拿到的也是 {} —— 期望失败集按依赖关系推全，别只写直接红的那条。
    其余仍绿：ok / resolved / listElements / capturePoll 都不依赖这行。

注入二（已捕获永远列空）：`for f in sorted(ed.glob("*.json")):` → `for f in []:`
    面板「已捕获」区永远空。期望红（恰好 2 条，连带断言同源）：
      - listElements 列出已捕获元素 captured_field
      - listElements 带了 kind/selector（列表空了它拿到的也是 {}，一并红）

注入三（未知会话不报错码）：UNKNOWN_CAPTURE → UNKNOWN_CAPTURE_X
    前端拿到的 ok=False 但 code 对不上，结构化错误契约被破坏。故意**不**删 None
    分支——那会让 `entry["proc"]` 对 None 取键直接 TypeError 崩掉，探针红成
    异常而不是判据；改码字符串才让断言以「判据不满足」的方式干净地红。
    期望红（恰好 1 条）：
      - capturePoll 未知会话 → 结构化 UNKNOWN_CAPTURE

用法：
    ./.venv/Scripts/python.exe ../../spike/probe_elements_negative.py
退出码 0 = 三条注入都被拦住且源码逐字节还原。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
PROBE_DIR = REPO / ".harness" / "demo" / "ppx_probe"
API = PROBE_DIR / "app" / "api" / "api.py"
PROBE = PROBE_DIR / "probe_elements.py"
PYTHON = PROBE_DIR / ".venv" / "Scripts" / "python.exe"
OUT = PROBE_DIR / "_elements_negative.out"

SENTINEL = "负向验证注入点"

INJECTIONS: list[dict] = [
    {
        "name": "待补永不报",
        "old": "            if name not in present:\n",
        "new": "            if False:  # " + SENTINEL + "\n",
        "expect_fail": {
            "missingElements 命中待补元素 missing_button",
            "待补条目带 nodeId/command/param（前端定位卡片就靠它）",
        },
    },
    {
        "name": "已捕获永远列空",
        "old": '        for f in sorted(ed.glob("*.json")):\n',
        "new": "        for f in []:  # " + SENTINEL + "\n",
        "expect_fail": {
            "listElements 列出已捕获元素 captured_field",
            "listElements 带了 kind/selector",
        },
    },
    {
        "name": "未知会话不报错码",
        "old": '        return {"ok": False, "code": "UNKNOWN_CAPTURE", "error": f"没有这个捕获会话：{name}"}\n',
        "new": '        return {"ok": False, "code": "UNKNOWN_CAPTURE_X", "error": f"没有这个捕获会话：{name}"}  # ' + SENTINEL + "\n",
        "expect_fail": {"capturePoll 未知会话 → 结构化 UNKNOWN_CAPTURE"},
    },
]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_probe() -> tuple[int, list[str], str]:
    """跑探针，返回 (rc, 变红的判据文案, 原始输出)。输出落文件，不接管道。"""
    with OUT.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            [str(PYTHON), str(PROBE)],
            cwd=str(PROBE_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    text = OUT.read_text(encoding="utf-8")
    failed = [
        line.split("]", 1)[1].rsplit("  ", 1)[0].strip()
        for line in text.splitlines()
        if line.startswith("[FAIL]")
    ]
    return proc.returncode, failed, text


def main() -> int:
    if API.read_text(encoding="utf-8").count(SENTINEL):
        print(f"源码里已有哨兵「{SENTINEL}」，拒绝运行：上次注入没还原")
        return 2

    original = API.read_bytes()
    baseline = md5(API)
    print(f"基线 md5 {baseline} · {len(original)} B")

    rc, failed, _ = run_probe()
    if rc != 0 or failed:
        print(f"基线就不绿（rc={rc}, 红={failed}），先修探针再谈负向验证")
        return 2
    print("基线：全绿\n")

    problems: list[str] = []
    try:
        for item in INJECTIONS:
            text = API.read_text(encoding="utf-8")
            hits = text.count(item["old"])
            if hits != 1:
                problems.append(f"{item['name']}：锚点命中 {hits} 次（要求 1 次），拒绝注入")
                print(f"[SKIP] {item['name']}：锚点命中 {hits} 次")
                continue
            API.write_bytes(text.replace(item["old"], item["new"]).encode("utf-8"))
            assert SENTINEL in API.read_text(encoding="utf-8"), "注入没落盘"

            rc, failed, _text = run_probe()
            got = set(failed)
            expected = item["expect_fail"]
            ok = rc != 0 and got == expected
            print(
                f"[{'PASS' if ok else 'FAIL'}] 注入「{item['name']}」→ rc={rc}，"
                f"红 {len(got)} 条：{sorted(got)}"
            )
            if not ok:
                print(f"        期望红：{sorted(expected)}")
                print(f"        实际红：{sorted(got)}")
                problems.append(f"{item['name']}：期望 {sorted(expected)}，实际 {sorted(got)}")

            API.write_bytes(original)
            restored = md5(API)
            ok_restore = restored == baseline
            print(f"[{'PASS' if ok_restore else 'FAIL'}] 逐字节还原（md5 {restored[:8]}）")
            if not ok_restore:
                problems.append(f"{item['name']}：还原后 md5 不等于基线")
    finally:
        # 无论中途怎样都把原文写回：宁可重复写一次，也不能把注入留在源码里
        if md5(API) != baseline:
            API.write_bytes(original)
            print(f"[兜底] 已还原到基线 md5 {md5(API)[:8]}")

    rc, failed, _ = run_probe()
    print(f"\n[{'PASS' if rc == 0 and not failed else 'FAIL'}] 复跑复绿（rc={rc}, 红={failed}）")
    if rc != 0 or failed:
        problems.append("还原后复跑不是全绿")

    if problems:
        print("\n未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n三条注入都被拦住，源码逐字节还原，复跑复绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
