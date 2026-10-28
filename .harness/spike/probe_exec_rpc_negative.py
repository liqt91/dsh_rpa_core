"""负向验证：把「运行接线」的两条判据各自打断一次，确认 exec_probe.py 真的会红。

不是「跑一遍探针绿了就放心」——绿的探针可能什么都没查。这里往 api.py 里精准注入
两处合法语法的破坏，期望**具体哪几条**判据变红，再逐字节还原并复核 md5。

注入一（丢 since 切片）：`"events": events[cursor:]` → `"events": events`
    界面会每轮都收到全部事件 ⇒ 日志重复刷。期望红：
      - 事件 seq 从 1 连续递增（拼出 [1,1,2] 这种）
      - 逐次拼接 == 全量，seq 不重不跳（多步流程拼出 5 倍重复）
      - since 越界回空列表而不是抛（越界也走同一处切片，丢掉就成了「返回全量」）
    第三条是第一轮负向验证时补充的：`since` 钳位与切片是同一行代码的两面，我最初
    只想到「重复」，没想到「越界」也依赖它 —— 期望失败集要按依赖关系推全，别照抄实际。

注入二（停止误报为故障）：把 `not entry["cancelled"]` 从条件里去掉
    用户点的停止会显示成「启动即失败」。期望只红一条：
      - 停止**不**报 startupError（用户停的不是故障）
    并且「标了 cancelled」仍然绿 —— 两件事本来就该分开判。

用法：
    ./.venv/Scripts/python.exe ../../spike/probe_exec_rpc_negative.py
退出码 0 = 两条注入都被拦住且源码逐字节还原。
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
PROBE = PROBE_DIR / "exec_probe.py"
PYTHON = PROBE_DIR / ".venv" / "Scripts" / "python.exe"
OUT = PROBE_DIR / "_negative.out"

SENTINEL = "负向验证注入点"

INJECTIONS: list[dict] = [
    {
        "name": "丢 since 切片",
        "old": '        "events": events[cursor:],\n',
        "new": '        "events": events,  # ' + SENTINEL + '\n',
        "expect_fail": {
            "事件 seq 从 1 连续递增",
            "逐次拼接 == 全量，seq 不重不跳",
            "since 越界回空列表而不是抛",
        },
    },
    {
        "name": "停止误报为启动失败",
        "old": '    if result is None and not running and not entry["cancelled"] and proc.returncode not in (0, None):\n',
        "new": '    if result is None and not running and proc.returncode not in (0, None):  # ' + SENTINEL + '\n',
        "expect_fail": {"停止**不**报 startupError（用户停的不是故障）"},
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
    print("\n两条注入都被拦住，源码逐字节还原，复跑复绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
