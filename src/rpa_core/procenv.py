"""子进程环境（M83）：让 ``python -m rpa_core.*`` 在**任何宿主**里都能 import。

为什么需要这一层：``sys.path`` 不出进程。宿主若用 ``sys.path.insert`` 把 ``src/``
挂进来（PPX 工作台就是这么做的，见 ``.harness/demo/ppx_probe/app/api/api.py``），
父进程里 ``import rpa_core`` 一切正常，而 ``subprocess.Popen([sys.executable, "-m",
"rpa_core.capture.desktop_agent"])`` 会在子进程里 ``ModuleNotFoundError`` 秒退。

2026-11-17 维护者报障就是这个形态：「捕获元素 / 按图捕获都只在网页框选，微信、
桌面没有任何反应」。桌面腿子进程一 spawn 就死，而浮窗照样写着「桌面：就绪」——
用户看不到任何原因。扩展腿（浏览器插件）不经过 Python 子进程，所以只有网页能用。

``PYTHONPATH`` 是唯一能跨进程传递导入路径的机制。**不要**在调用点各写一份拼接：
本仓有四处 ``-m rpa_core.*`` 的子进程（捕获 agent、点击钩子、OCR worker、Python
worker），漏掉一处就是一条静默失效的腿。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def import_root() -> str | None:
    """``rpa_core`` 包所在目录的父目录（即 ``src/``）；取不到时返回 ``None``。

    打包（frozen）态返回 ``None``：那时包在 ``_MEIPASS`` 里，子进程是同一个 exe、
    不需要 ``PYTHONPATH``，硬塞进去反而可能把无关目录带上导入路径。
    """
    if getattr(sys, "frozen", False):
        return None
    module = sys.modules.get("rpa_core")
    location = getattr(module, "__file__", None)
    if not location:
        return None
    return str(Path(location).resolve().parent.parent)


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """返回一份**可直接传给 Popen(env=...)** 的环境。

    - 保留 ``base``（缺省取当前 ``os.environ``）里的全部键，不裁剪；
    - 把 ``rpa_core`` 的导入根**前置**到 ``PYTHONPATH``，已存在则不重复追加
      （Windows 路径大小写不敏感，按 ``normcase`` 比较）。
    """
    env = dict(os.environ if base is None else base)
    root = import_root()
    if root is None:
        return env
    existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    wanted = os.path.normcase(root)
    if all(os.path.normcase(p) != wanted for p in existing):
        existing.insert(0, root)
    env["PYTHONPATH"] = os.pathsep.join(existing)
    return env
