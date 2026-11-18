"""负向验证：examples 数量判据真的拦得住 WorkBuddy 技能解析的「最多 3 条」。

平台实测报错：「examples_zh 最多 3 个示例，当前 5 个 / examples_en 最多 3 个示例，当前 5 个」。
原判据写的是 `len(examples) >= 3` 且只查 zh —— 4/5 条照样绿，en 超限完全看不见。
本探针把判据改成两键成对、等值断言后，证明：

  A. zh 4 条 → 只有 examples_zh 红
  B. en 5 条 → 只有 examples_en 红
  C. zh 2 条 → 只有 examples_zh 红（不是只拦上界）
  D. 元验证：把新判据临时退回 `>= 3`，同样的 zh 4 条必须变全绿
     ⇒ 上面的红是新判据报的，不是别的东西顺带红的

纪律：期望失败集按推理写成等值集合；不删文件（会撞删除守卫）；所有注入包在
try/finally 里逐字节还原并核 md5；不接管道（BrokenPipe 会打断还原）。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_examples_limit_negative.py
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "workbuddy-suite"
SKILL_MD = SUITE / "skills" / "workflow-studio" / "SKILL.md"
VERIFY = SUITE / "verify" / "verify_suite.py"

sys.path.insert(0, str(SUITE / "verify"))
import verify_suite as vs  # noqa: E402

OLD_ASSERT = "len(examples) == 3"
NEW_ASSERT = "len(examples) >= 3"
ZH = "examples_zh 恰好 3 条"
EN = "examples_en 恰好 3 条"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def with_count(text: str, key: str, count: int) -> str:
    """把某个 examples 块的条数改成 count（合法 YAML，只重复已有条目）。"""
    pattern = re.compile(r"(" + key + r":\n)(?:  - [^\n]*\n)+")
    match = pattern.search(text)
    if not match:
        raise AssertionError(f"没找到 {key} 的列表块")
    items = re.findall(r"  - [^\n]*\n", match.group(0)[len(key) + 1 :])
    if not items:
        raise AssertionError(f"{key} 的列表块是空的")
    made = "".join((items * count)[:count])
    out = text[: match.start()] + match.group(1) + made + text[match.end() :]
    # 注入自证：锚点没命中时静默返回原文会让「没红」被误读成判据没用。
    written = re.search(key + r":\n(?:  - [^\n]*\n)+", out)
    assert written, f"{key} 注入后找不到列表块"
    assert len(re.findall(r"  - [^\n]*\n", written.group(0))) == count, (
        f"{key} 注入后条数不是 {count}"
    )
    return out


def run_check() -> dict[str, bool]:
    vs.RESULTS.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        vs.check_skill()
    return {name: ok for name, ok, _ in vs.RESULTS}


def expect(result: dict[str, bool], failed: set[str], baseline: set[str], label: str) -> None:
    actual = {name for name, ok in result.items() if not ok}
    assert set(result) == baseline, f"{label}: check 集合变了 → {set(result) ^ baseline}"
    assert actual == failed, f"{label}: 期望红 {sorted(failed)}，实际红 {sorted(actual)}"


def main() -> int:
    skill_bytes = SKILL_MD.read_bytes()
    verify_bytes = VERIFY.read_bytes()
    skill_text = skill_bytes.decode("utf-8")
    verify_text = verify_bytes.decode("utf-8")

    # 哨兵：上一轮异常退出把注入留在源码里的话，这一轮直接拒绝运行。
    assert verify_text.count(OLD_ASSERT) == 1, "判据锚点不唯一或已被改过，先还原源码"
    for key in ("examples_zh", "examples_en"):
        block = re.search(key + r":\n(?:  - [^\n]*\n)+", skill_text)
        assert block, f"没找到 {key}"
        assert len(re.findall(r"  - [^\n]*\n", block.group(0))) == 3, (
            f"{key} 不是 3 条，疑似上一轮注入没还原"
        )

    baseline = set(run_check())
    assert not [n for n, ok in run_check().items() if not ok], "基线本身就是红的，别往下跑"
    assert ZH in baseline and EN in baseline, f"判据项没进 check 集合：{sorted(baseline)}"

    try:
        # A / B / C：注入 → 跑 → 等值断言
        for label, key, count, want in (
            ("A zh=4", "examples_zh", 4, {ZH}),
            ("B en=5", "examples_en", 5, {EN}),
            ("C zh=2", "examples_zh", 2, {ZH}),
        ):
            payload = with_count(skill_text, key, count)
            SKILL_MD.write_bytes(payload.encode("utf-8"))
            assert SKILL_MD.read_text(encoding="utf-8") == payload, f"{label}: 注入没落盘"
            print(f"[INJECTED] {label}: {key}={count}（读回确认）")
            expect(run_check(), want, baseline, label)
            print(f"[OK] {label} → 红 {sorted(want)}")

        # D 元验证：关掉新判据，同样的 4 条必须全绿
        assert verify_text.count(OLD_ASSERT) == 1, "元验证锚点不唯一"
        VERIFY.write_bytes(verify_text.replace(OLD_ASSERT, NEW_ASSERT, 1).encode("utf-8"))
        importlib.reload(vs)
        SKILL_MD.write_bytes(with_count(skill_text, "examples_zh", 4).encode("utf-8"))
        relaxed = run_check()
        expect(relaxed, set(), baseline, "D 元验证(>= 3)")
        print("[OK] D 元验证：判据退回 >= 3 后 4 条重新全绿")
    finally:
        SKILL_MD.write_bytes(skill_bytes)
        VERIFY.write_bytes(verify_bytes)
        importlib.reload(vs)

    assert md5(SKILL_MD.read_bytes()) == md5(skill_bytes), "SKILL.md 没逐字节还原"
    assert md5(VERIFY.read_bytes()) == md5(verify_bytes), "verify_suite.py 没逐字节还原"
    expect(run_check(), set(), baseline, "还原后复绿")
    print("NEGATIVE OK — 3 注入 + 1 元验证，源码逐字节还原")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
