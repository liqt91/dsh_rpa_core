"""M83：``child_env`` 是「子进程能不能 import rpa_core」的唯一权威。

背景（2026-11-17 报障）：PPX 工作台用 ``sys.path.insert`` 挂 ``src/``，子进程不继承
``sys.path``。桌面捕获 agent 一 spawn 就 ``ModuleNotFoundError`` 秒退，而父进程里
``import rpa_core`` 完全正常 —— 没有任何异常可以捕获，只有「按 F9 没反应」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rpa_core.procenv import child_env, import_root


def test_import_root_points_at_src() -> None:
    root = import_root()
    assert root is not None
    # 该目录下必须真的能直接看到 rpa_core 包
    assert (Path(root) / "rpa_core" / "__init__.py").is_file()


def test_child_env_prepends_root_when_absent() -> None:
    env = child_env({"PATH": "x", "PYTHONPATH": ""})
    assert env["PATH"] == "x"  # 其余变量原样保留，不裁剪
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == import_root()


def test_child_env_keeps_existing_entries_and_does_not_duplicate() -> None:
    root = import_root()
    base = {"PYTHONPATH": os.pathsep.join(["D:/somewhere", root])}
    env = child_env(base)
    parts = env["PYTHONPATH"].split(os.pathsep)
    # 已经在了就不再追加：重复项会让 sys.path 膨胀，也会掩盖「到底谁提供包」
    assert parts.count(root) == 1
    assert "D:/somewhere" in parts


def test_child_env_is_noop_when_frozen(monkeypatch) -> None:
    """打包态：包在 _MEIPASS 里，子进程是同一个 exe，不该被塞 PYTHONPATH。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = child_env({"PYTHONPATH": ""})
    assert env["PYTHONPATH"] == ""


def test_child_env_copies_instead_of_mutating_caller() -> None:
    base = {"PYTHONPATH": ""}
    child_env(base)
    assert base["PYTHONPATH"] == ""
