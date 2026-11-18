"""M85：agent 里的未处理异常必须变成**结构化原因**，而不是一句「进程起不来」。

2026-11-17：图像模式 hover 循环抛 AttributeError ⇒ agent 秒退（rc=1）⇒ 父进程只
读得到 ``desktop capture agent exited with code 1`` ⇒ 浮窗写「捕获进程起不来」，
用户去查环境。真凶是本进程里的一行 bug。

约定：捕获期的**未处理异常**不再以非零退出码裸奔，而是打印一行
``{"error": "桌面捕获内部错误：<异常类型>: <消息>"}``（rc=0），完整 traceback 仍写
stderr 供取证。这样 ① 浮窗能显示确切原因 ② ``HybridCaptureSession`` 记下的
``desktop_failure`` 里有可读文本。
"""

from __future__ import annotations

import json
import sys
import types

from rpa_core.capture import desktop_agent


def _fake_pythoncom() -> types.SimpleNamespace:
    """免掉真 COM 初始化：本用例只关心异常如何回传。"""

    return types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)


def _boom(*_args: object, **_kwargs: object) -> dict:
    raise AttributeError("'tuple' object has no attribute 'left'")


def test_unhandled_crash_is_reported_as_structured_error(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "pythoncom", _fake_pythoncom())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(desktop_agent, "_hover_capture", _boom)
    monkeypatch.setattr(
        sys, "argv", ["desktop-capture-agent", "--hover", "--image", "--timeout", "1"]
    )

    rc = desktop_agent.main()
    captured = capsys.readouterr()

    assert rc == 0, "崩溃不能再以非零退出码裸奔——那正是「进程起不来」的来源"
    payload = json.loads(captured.out.strip())
    assert "内部错误" in payload["error"]
    assert "AttributeError" in payload["error"], "不回传异常类型，用户仍然无从下手"
    assert "no attribute 'left'" in payload["error"]
    # 栈要留在 stderr 给取证用，不能被吞
    assert "Traceback" in captured.err


def test_successful_run_still_emits_only_the_result(monkeypatch, capsys) -> None:
    """对照组：正常返回时 stdout 只有一行结果，别把错误分支的产物混进去。"""
    monkeypatch.setitem(sys.modules, "pythoncom", _fake_pythoncom())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        desktop_agent, "_hover_capture", lambda *a, **k: {"cancelled": True}
    )
    monkeypatch.setattr(sys, "argv", ["desktop-capture-agent", "--hover"])

    assert desktop_agent.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out.strip()) == {"cancelled": True}
    assert captured.err == ""
