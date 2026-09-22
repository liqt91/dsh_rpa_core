"""L1 契约矩阵驱动：读用例表 JSON → 参数化执行 → 断言真实下发的调用与结果。

用例表是**数据**（`cases/<namespace>.json`），本文件只是解释器：新增命令覆盖时改 JSON，
不动代码。表与 catalog 的对齐由 `.harness/scripts/check_command_matrix.py` 静态校验
（在默认门禁里）——本文件按需跑（`RPA_COMMAND_MATRIX=1`，见 `conftest.py`）。

`expect` 支持的键（全部可选，按需组合）：

| 键 | 含义 |
|---|---|
| `noCalls` | 断言没有向扩展下发任何命令（本地操作，如 `browser.close`） |
| `onlyCall` | 断言**恰好一次**下发；`op`、`args`（子集）、`argsExact`（等值）、`timeoutSeconds` |
| `calls` | 断言完整调用序列（逐项同 `onlyCall` 的语义） |
| `outputs` | `result.outputs` 的子集 |
| `outputKeys` | `result.outputs` 必须包含的键（值不确定时用，如 uuid 形式的 sessionId） |
| `effect` | `effects[0].kind` |
| `errorCode` / `errorDetails` | 失败路径：错误码（精确）/ details（子集） |
| `elapsedAtLeastMs` | 整条命令的墙钟耗时下界（验证 `postDelayMs` 这类「只花时间」的参数） |

变体可选的键：`inputs` / `sessions`（覆盖默认会话表）/ `stub`（覆盖命令级桩应答）/
`errors`（op → 扩展侧错误）/ `processStub`（打桩进程面，`browser.closeBrowser` 专用）/
`negative`（标记负路径/边界变体，供覆盖率校验器统计）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import rpa_core.executors.browser as browser_module
from rpa_core.extension_exec import ExtensionChannelError
from tests.commands.harness import ScriptedExtension, run_command

CASES_DIR = Path(__file__).resolve().parent / "cases"


def _load_cases() -> dict[str, Any]:
    """合并 `cases/*.json`（每个文件是一个命名空间）。"""
    merged: dict[str, Any] = {}
    for path in sorted(CASES_DIR.glob("*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def _iter_variants():
    for command, spec in sorted(_load_cases().items()):
        for variant in spec.get("variants", []):
            yield pytest.param(
                command, spec, variant, id=f"{command}::{variant['name']}"
            )


def _subset_problems(actual: Any, expected: Any, path: str = "args") -> list[str]:
    """`expected` 必须是 `actual` 的子集（递归）。返回违规说明列表（空 = 通过）。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 期望 dict，实得 {type(actual).__name__}：{actual!r}"]
        problems: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"{path}.{key}: 缺失（实际键 {sorted(actual)}）")
                continue
            problems.extend(_subset_problems(actual[key], value, f"{path}.{key}"))
        return problems
    if actual != expected:
        return [f"{path}: 期望 {expected!r}，实得 {actual!r}"]
    return []


def _check_call(actual, expected: dict[str, Any], path: str) -> list[str]:
    problems: list[str] = []
    if "op" in expected and actual.op != expected["op"]:
        problems.append(f"{path}.op: 期望 {expected['op']!r}，实得 {actual.op!r}")
    if "timeoutSeconds" in expected:
        wanted_s = float(expected["timeoutSeconds"])
        if abs(actual.timeout_seconds - wanted_s) > 1e-6:
            problems.append(
                f"{path}.timeoutSeconds: 期望 {wanted_s}，实得 {actual.timeout_seconds}"
            )
    if "argsExact" in expected:
        if actual.args != expected["argsExact"]:
            problems.append(
                f"{path}.args: 期望完全相等 {expected['argsExact']!r}，实得 {actual.args!r}"
            )
    elif "args" in expected:
        problems.extend(_subset_problems(actual.args, expected["args"], f"{path}.args"))
    return problems


def _install_process_stub(monkeypatch, stub: dict[str, Any]) -> list[bool]:
    """打桩进程面（`browser.closeBrowser`），返回每次终止收到的 force 实参。

    conftest 的 autouse 守卫已经把这条面钉成「拦下」，这里在用例内覆盖它——
    依据是「要验证破坏性路径的用例自己显式打桩」。
    """
    pids = [int(pid) for pid in stub.get("pids") or []]
    seen_force: list[bool] = []
    monkeypatch.setattr(
        browser_module,
        "_list_browser_processes",
        lambda names: [{"pid": pid, "name": "probe.exe"} for pid in pids],
    )
    monkeypatch.setattr(browser_module, "_wait_processes_exit", lambda pids_, timeout_s: True)

    def _terminate(pid: int, force: bool) -> None:
        seen_force.append(bool(force))

    monkeypatch.setattr(browser_module, "_terminate_process", _terminate)
    return seen_force


@pytest.fixture(scope="session")
def matrix_tmp_dir():
    """整个矩阵共用的临时目录，供用例表里的 `{tmp}` 占位符落盘。

    刻意**不用** pytest 的 `tmp_path`：那会为每个用例各建一个编号目录（本矩阵 150+ 个
    用例），会话结束时 pytest 在 atexit 里批量删除，在受限执行环境会被批量删除守卫拦下
    并以 `SystemExit(1)` 收场——**测试全绿却让整个门禁看起来失败**（2026-09-22 实测）。
    共用一个目录后，pytest 连 basetemp 都不必创建。
    """
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="rpa-command-matrix-"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _materialize_inputs(inputs: Any, tmp_dir: Path) -> Any:
    """把用例表里的 `{tmp}` 占位符替换成本次运行的临时目录。

    用例表是纯数据、不能写死本机路径；`browser.screenshot` 这类**真的落盘**的命令
    必须写到 pytest 的 `tmp_path`，否则会污染仓库工作树。
    """
    if isinstance(inputs, str):
        return inputs.replace("{tmp}", str(tmp_dir))
    if isinstance(inputs, dict):
        return {key: _materialize_inputs(value, tmp_dir) for key, value in inputs.items()}
    if isinstance(inputs, list):
        return [_materialize_inputs(value, tmp_dir) for value in inputs]
    return inputs


def _build_errors(spec: dict[str, Any] | None) -> dict[str, Exception]:
    errors: dict[str, Exception] = {}
    for op, body in (spec or {}).items():
        errors[op] = ExtensionChannelError(
            str(body.get("code") or "EXECUTOR_FAILED"),
            str(body.get("message") or "stubbed failure"),
        )
    return errors


@pytest.mark.parametrize("command, spec, variant", list(_iter_variants()))
def test_command_variant(monkeypatch, matrix_tmp_dir, command, spec, variant):
    expect = variant["expect"]
    replies = {**spec.get("stub", {}), **variant.get("stub", {})}
    ext = ScriptedExtension(replies=replies, errors=_build_errors(variant.get("errors")))

    seen_force = (
        _install_process_stub(monkeypatch, variant["processStub"])
        if "processStub" in variant
        else []
    )

    sessions = variant["sessions"] if "sessions" in variant else None
    inputs = _materialize_inputs(variant.get("inputs", {}), matrix_tmp_dir)
    started = time.perf_counter()
    result = run_command(ext, command, inputs, sessions=sessions)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    problems: list[str] = []

    # -- 调用面 --------------------------------------------------------------
    if expect.get("noCalls"):
        if ext.calls:
            problems.append(f"期望不下发任何扩展命令，实得 {ext.ops}")
    if "onlyCall" in expect:
        if len(ext.calls) != 1:
            problems.append(f"期望恰好 1 次下发，实得 {len(ext.calls)}：{ext.ops}")
        else:
            problems.extend(_check_call(ext.calls[0], expect["onlyCall"], "onlyCall"))
    if "calls" in expect:
        if len(ext.calls) != len(expect["calls"]):
            problems.append(
                f"调用次数：期望 {len(expect['calls'])}，实得 {len(ext.calls)}：{ext.ops}"
            )
        else:
            for index, (actual, wanted) in enumerate(
                zip(ext.calls, expect["calls"], strict=True)
            ):
                problems.extend(_check_call(actual, wanted, f"calls[{index}]"))

    # -- 结果面 --------------------------------------------------------------
    if "errorCode" in expect:
        if result.status != "error" or result.error is None:
            problems.append(f"期望失败（{expect['errorCode']}），实得 status={result.status}")
        elif result.error.code != expect["errorCode"]:
            problems.append(
                f"errorCode：期望 {expect['errorCode']!r}，实得 {result.error.code!r}"
                f"（消息：{result.error.message[:80]}）"
            )
        elif "errorDetails" in expect:
            problems.extend(
                _subset_problems(
                    result.error.details or {}, expect["errorDetails"], "errorDetails"
                )
            )
    elif result.status != "success":
        detail = result.error.message if result.error else ""
        problems.append(f"期望成功，实得 status={result.status}：{detail[:120]}")

    if "outputs" in expect:
        problems.extend(_subset_problems(result.outputs, expect["outputs"], "outputs"))
    if "outputKeys" in expect:
        missing = [key for key in expect["outputKeys"] if key not in result.outputs]
        if missing:
            problems.append(f"outputs 缺少键 {missing}（实际 {sorted(result.outputs)}）")
    if "effect" in expect:
        kinds = [effect.kind.value for effect in result.effects]
        if not kinds:
            problems.append(f"期望 effect={expect['effect']}，但没有任何 effect 记录")
        elif kinds[0] != expect["effect"]:
            problems.append(f"effect：期望 {expect['effect']!r}，实得 {kinds[0]!r}")
    if "elapsedAtLeastMs" in expect and elapsed_ms < float(expect["elapsedAtLeastMs"]):
        problems.append(
            f"耗时下界：期望 ≥{expect['elapsedAtLeastMs']}ms，实得 {elapsed_ms:.1f}ms"
        )

    # -- 进程面（force 实参） ------------------------------------------------
    if variant.get("processStub", {}).get("expectForce") and not all(seen_force):
        problems.append(f"期望以 force=True 终止，实得 {seen_force}")

    assert not problems, "\n".join(["用例断言失败：", *problems])
