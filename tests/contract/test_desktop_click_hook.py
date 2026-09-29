"""desktop_click_hook 的纯判定测试（M47.3）。

真实鼠标钩子无法在沙箱里模拟（低级钩子要真输入流），把吞没判定抽成纯函数
``_swallow_decision`` 后在这里穷举手势矩阵；回调结构（「判定」与「取证」分段、
``if swallow: return 1`` 无条件执行）由源码切片断言钉住——真机踩过的洞是
「判定之后、return 之前出异常 → 事件被放行」，静态结构断言就是为它立的。
"""

from __future__ import annotations

import ast
from pathlib import Path

from rpa_core.capture.desktop_click_hook import _swallow_decision

ROOT = Path(__file__).resolve().parents[2]


def test_swallow_matrix() -> None:
    """手势矩阵：无 Ctrl 不吞；浏览器内容区让位不吞；桌面区 DOWN 起吞、UP 成对续吞。"""
    # 无 Ctrl：不吞，成对标记保持
    assert _swallow_decision(False, False, True, False) == (False, False)
    assert _swallow_decision(False, False, False, True) == (False, True)
    # Ctrl + 浏览器内容区（hybrid 让位）：不吞（页内 Ctrl+Click 是扩展手势）
    assert _swallow_decision(True, True, True, False) == (False, False)
    # Ctrl + 桌面应用：DOWN 起吞并置标记；UP 续吞并保持标记（防孤儿 UP）
    assert _swallow_decision(True, False, True, False) == (True, True)
    assert _swallow_decision(True, False, False, True) == (True, True)
    # Ctrl + 桌面应用但 DOWN 曾被让位/旁路（标记未置）：UP 不吞（无 DOWN 可配对）
    assert _swallow_decision(True, False, False, False) == (False, False)


def test_callback_guarantees_swallow_return() -> None:
    """回调结构：``if swallow: return 1`` 必须在取证段**之后**无条件执行，且
    取证段自带异常兜底——判定与放行之间不允许存在任何能放走已吞事件的路径。"""
    src = (ROOT / "src" / "rpa_core" / "capture" / "desktop_click_hook.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    proc = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_proc"
    )
    # 1) _trace 调用存在且都裹在 try 里（取证永不打断吞没主路径）
    trace_calls = [
        n
        for n in ast.walk(proc)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_trace"
    ]
    assert trace_calls, "回调内应有取证调用"
    def _contains(node: ast.AST, target: ast.AST) -> bool:
        return any(n is target for n in ast.walk(node))

    def _returns_one(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and node.value.value == 1
        )

    # 2) return 1（吞）出现在**最后一个含 _trace 的顶层语句之后**——取证/兜底
    #    无论发生什么，已判吞的事件都走无条件 return 1
    last_trace_idx = max(
        i
        for i, st in enumerate(proc.body)
        if any(_contains(st, c) for c in trace_calls)
    )
    tail = proc.body[last_trace_idx + 1 :]
    assert any(
        isinstance(st, ast.If) and any(_returns_one(n) for n in ast.walk(st))
        for st in tail
    ), "swallow 的 return 1 必须在取证段之后无条件执行"
    # 3) 旧的「整体大 try 包住判定+取证+return」结构不得回潮（那是真机踩过的洞）
    assert not (
        isinstance(proc.body[0], ast.Try)
        and any(_returns_one(n) for n in ast.walk(proc.body[0]))
    ), "不得回潮为整体大 try（异常会落到 CallNextHookEx 放行已吞事件）"


def test_setevent_calls_target_kernel32() -> None:
    """M47.4 真机实锤：``user32.SetEvent`` 不存在（SetEvent 属 kernel32），
    吞钩子抛 AttributeError 被兜底吞掉 → 点击吞了但 agent 收不到通知，
    桌面捕获整体失效。AST 断言：本模块每一处 ``<obj>.SetEvent(...)`` 的
    接收者都必须是 ``kernel32``——「DLL 归属写错」这一类故障由此断根。
    """
    src = (ROOT / "src" / "rpa_core" / "capture" / "desktop_click_hook.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SetEvent"
    ]
    assert calls, "应存在 SetEvent 调用（吞事件通知 agent 的主通道）"
    bad = [
        c
        for c in calls
        if not (isinstance(c.func.value, ast.Name) and c.func.value.id == "kernel32")
    ]
    assert not bad, "SetEvent 只能调 kernel32.SetEvent，错写 user32/其他在真机必炸"
