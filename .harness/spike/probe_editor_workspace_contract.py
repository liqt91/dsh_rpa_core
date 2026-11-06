"""M67 契约判据的负向验证：9 组注入，逐条核对「红在哪一条」。

纪律（gate-negative-verification）：
- 期望失败集按推理写，不照抄实际；跑完逐条比对，多一条少一条都要查。
- 每组注入都是合法语法的精准放宽/破坏，不制造 SyntaxError 假红。
- 清单 + 文件末尾哨兵双凭证；try/finally 逐组还原，核 md5，不用 unlink。
- 对照跑要求 passed>=1；每组要求 passed>=1 + 输出末行是 CHECK FAILED（防 0 收集 / 崩溃）。

运行：./.venv/Scripts/python.exe .harness/spike/probe_editor_workspace_contract.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / ".harness" / "scripts" / "check_editor_workspace_contract.py"
STATE = ROOT / ".harness" / "spike" / "_m67_nv_state.json"
SENTINEL = "NV-M67-INJECTED"

FILES = {
    "api": ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py",
    "js": ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js",
    "studio": ROOT / "workbuddy-suite" / "skills" / "workflow-studio" / "scripts" / "studio.py",
}

# 判据输出里的用例名，必须与 check_editor_workspace_contract.py 的 for 循环一致
C_REL = "相对路径仍按 WORKSPACE 打开"
C_ABS = "同根绝对路径可打开（会话→窗口）"
C_CJK = "中文会话根 + 中文绝对路径可打开"
C_OUT = "越界绝对路径返回结构化 BAD_PATH"
C_MISS = "文件不存在也返回 BAD_PATH 并说明"
C_BRIDGE = "桥等价包装不再吞成 INTERNAL_ERROR"
C_GUARD = "拿掉会话根后守卫确实会拒（防假绿）"
C_LOG = "冻结态异常落盘且带 traceback"
C_GUI = "cmd_gui 注入同一个 root / 无 root 不造根"
C_GUI2 = "cmd_gui 连已有服务时对齐它的 root"
C_WAIT = "cmd_open --wait 立刻回传 BAD_PATH"
C_FRONT = "前端把失败原因回进 open-failed"
C_SRC = "源码接线闸：无裸异常 / env 透传 / 等待集合"

CASES = [
    {
        "name": "① 越界异常换回裸 ValueError（桥会吞成 INTERNAL_ERROR）",
        "file": "api",
        "old": 'raise BridgeError("BAD_PATH", _bad_path_message(rel, roots, "路径越界"))',
        "new": 'raise ValueError("路径越界")  # NV-M67-INJECTED',
        "expect": {C_OUT, C_BRIDGE, C_GUARD, C_SRC},
    },
    {
        "name": "② BAD_PATH 原因退化成通用文案（根与修法都没了）",
        "file": "api",
        "old": '    expected = " 或 ".join(str(root) for root in roots)\n    return (',
        "new": (
            '    expected = " 或 ".join(str(root) for root in roots)\n'
            '    return "路径错误"  # NV-M67-INJECTED\n'
            '    return ('
        ),
        "expect": {C_OUT, C_MISS, C_BRIDGE},
    },
    {
        "name": "③ 窗口启动不再注入 RPA_WORKSPACE",
        "file": "studio",
        "old": '    if root is not None:\n        child_env["RPA_WORKSPACE"] = str(root)',
        "new": '    if False:  # NV-M67-INJECTED\n        child_env["RPA_WORKSPACE"] = str(root)',
        "expect": {C_GUI, C_GUI2},
    },
    {
        "name": "④ 前端 openFlow 又把失败吞掉",
        "file": "js",
        "old": "    return { ok: false, error: failure }",
        "new": "    return undefined  // NV-M67-INJECTED",
        "expect": {C_FRONT},
    },
    {
        "name": "⑤ --wait 的等待集合丢掉 open-failed",
        "file": "studio",
        "old": '{"save", "close", "open-failed"}',
        "new": '{"save", "close"}',
        "expect": {C_WAIT, C_SRC},
    },
    {
        "name": "⑥ 冻结态异常日志不落盘（handler 没挂上）",
        "file": "api",
        "old": "        logger.addHandler(handler)",
        "new": "        handler.close()  # NV-M67-INJECTED",
        "expect": {C_LOG},
    },
    {
        "name": "⑦ 越界守卫改成恒真（任何路径都放行）",
        "file": "api",
        "old": "    if not any(target.is_relative_to(root) for root in roots):",
        "new": "    if False:  # NV-M67-INJECTED",
        "expect": {C_OUT, C_BRIDGE, C_GUARD},
    },
    {
        "name": "⑧ 读侧：会话根不再进允许根（中文绝对路径应被拒）",
        "file": "api",
        "old": "    if session is not None and session not in roots:\n        roots.append(session)",
        "new": "    if False:  # NV-M67-INJECTED\n        roots.append(session)",
        "expect": {C_CJK},
    },
    {
        "name": "⑨ 连已有服务时不读 /health 的 root（只对齐显式 --root）",
        "file": "studio",
        "old": "                if raw:\n                    root = Path(raw).resolve()",
        "new": "                if False:  # NV-M67-INJECTED\n                    root = Path(raw).resolve()",
        "expect": {C_GUI2},
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def restore(state: list[dict]) -> None:
    for item in state:
        path = Path(item["path"])
        path.write_text(item["original"], encoding="utf-8", newline="\n")
        after = md5(path.read_bytes())
        assert after == item["md5_before"], f"还原后 md5 不等：{path} {after} != {item['md5_before']}"
    STATE.write_text("[]", encoding="utf-8", newline="\n")


def self_heal() -> None:
    """起手先看有没有上一轮被 kill 留下的哨兵；只还原「清单里有 + 文件确实带哨兵」的。"""
    dirty = [
        path for path in FILES.values()
        if SENTINEL in path.read_text(encoding="utf-8")
    ]
    if not dirty:
        return
    if not STATE.is_file():
        raise SystemExit(f"[NV] 发现哨兵但没有清单，拒绝运行：{[str(p) for p in dirty]}")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    recorded = {item["path"] for item in state}
    for path in dirty:
        if str(path) not in recorded:
            raise SystemExit(f"[NV] 带哨兵的文件不在清单里，拒绝还原：{path}")
    restore(state)
    print(f"[NV] 自愈还原 {len(state)} 个文件")


def run_gate() -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, str(GATE)],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"timeout": True, "passed": [], "failed": [], "rc": None, "ok": False, "out": ""}
    out = proc.stdout + "\n" + proc.stderr
    passed = [row[5:].strip() for row in out.splitlines() if row.startswith("PASS ")]
    failed = [row[5:].split(" — ")[0].strip() for row in out.splitlines()
              if row.startswith("FAIL ")]
    return {
        "timeout": False,
        "passed": passed,
        "failed": set(failed),
        "rc": proc.returncode,
        "ok": "EDITOR WORKSPACE CONTRACT CHECK PASSED" in out,
        "red": "EDITOR WORKSPACE CONTRACT CHECK FAILED" in out,
        "out": out,
    }


def main() -> int:
    self_heal()
    baseline = run_gate()
    assert not baseline["timeout"], "[NV] 对照跑超时（判据挂住了）"
    assert baseline["rc"] == 0 and baseline["ok"], f"[NV] 对照跑不绿：{baseline['out'][-800:]}"
    assert len(baseline["passed"]) >= 1, "[NV] 对照跑 0 收集，选择器/加载有问题"
    total = len(baseline["passed"]) + len(baseline["failed"])
    print(f"[NV] 对照：{total} 条全绿，passed={len(baseline['passed'])}")

    report: list[dict] = []
    problems: list[str] = []
    for case in CASES:
        path = FILES[case["file"]]
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        assert case["old"] in text, f"[NV] 锚点未命中：{case['name']}"
        state = [{
            "path": str(path),
            "original": text,
            "md5_before": md5(raw),
            "sentinel": SENTINEL,
        }]
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8", newline="\n")
        try:
            tail = "# NV-M67-INJECTED\n" if path.suffix == ".py" else "// NV-M67-INJECTED\n"
            injected = text.replace(case["old"], case["new"], 1)
            assert case["new"] in injected, "替换没生效"
            path.write_text(injected + tail, encoding="utf-8", newline="\n")
            assert SENTINEL in path.read_text(encoding="utf-8"), "哨兵没写进去"
            result = run_gate()
            expected = case["expect"]
            actual = result["failed"]
            verdict = "OK"
            if result["timeout"]:
                verdict = "TIMEOUT（判据挂住）"
            elif not result["red"] or result["rc"] == 0 or len(result["passed"]) < 1:
                verdict = "崩溃/0 收集（不是断言红）"
            elif actual != expected:
                verdict = f"红集合不符（多={sorted(actual - expected)} 少={sorted(expected - actual)}）"
            elif "SyntaxError" in result["out"]:
                verdict = "注入制造了语法错误"
            if verdict != "OK":
                problems.append(f"{case['name']}: {verdict}\n{result['out'][-600:]}")
            report.append({
                "name": case["name"],
                "verdict": verdict,
                "red": sorted(actual),
                "expected": sorted(expected),
            })
            print(f"[NV] {case['name']} → {verdict}")
            for name in sorted(actual):
                print(f"      红：{name}")
        finally:
            restore(state)
        assert SENTINEL not in path.read_text(encoding="utf-8"), f"[NV] 没还原干净：{path}"
        assert md5(path.read_bytes()) == md5(raw), f"[NV] md5 没还原：{path}"

    final = run_gate()
    assert final["rc"] == 0 and final["ok"] and not final["failed"], \
        f"[NV] 还原后判据没全绿：{final['out'][-800:]}"
    print(f"[NV] 还原后复跑：{len(final['passed'])}/{total} 全绿，md5 逐组核对通过")
    (ROOT / ".harness" / "spike" / "_m67_nv_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    if problems:
        print("\n".join(problems))
        print(f"[NV] NEGATIVE VERIFICATION FAILED ({len(problems)}/{len(CASES)})")
        return 1
    print(f"[NV] NEGATIVE VERIFICATION PASSED ({len(CASES)}/{len(CASES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
