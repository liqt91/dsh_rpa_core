"""GUI 窗口状态持久化（M49 P1-1）。

只做三件事：把「窗口几何 / 分栏比例 / 对话框尺寸」这类**用户调过一次就不想再调**
的状态存进 QSettings，下次启动还原；没有记录时用调用方给的默认值。

为什么显式指定 IniFormat + UserScope，而不是 `QSettings()` 走默认原生后端：

1. 配置落在可读的 ini 文件里（而不是 Windows 注册表），排查「界面怎么变成这样了」
   时能直接看；
2. 测试可以用 ``QSettings.setPath(IniFormat, UserScope, tmp)`` 一次性把整个 GUI 套件
   的设置隔离到临时目录——否则测试会读写字机上的真实配置，出现「本机能过、
   CI 上红」这类环境耦合（M47.5b 已吃过一次同类教训）。

取值一律经过 :func:`parse_sizes` / :func:`parse_size` 归一：QSettings 在不同平台上
可能回 list[int]、list[str] 或字符串，坏值一律当「没有记录」处理——宁可用默认值，
也不要因为一份坏配置让窗口开不出来。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

ORG = "rpa-core"
APP = "editor"


def gui_settings() -> QSettings:
    """GUI 设置后端（显式 IniFormat/UserScope，见模块 docstring）。"""
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG, APP)


def parse_sizes(value: object, *, expected: int) -> list[int] | None:
    """把 QSettings 取回的值归一成 ``expected`` 个正整数；不合规返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        parts: list[object] = [p for p in value.replace(",", " ").split() if p]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return None
    try:
        sizes = [int(p) for p in parts]
    except (TypeError, ValueError):
        return None
    if len(sizes) != expected or any(size <= 0 for size in sizes):
        return None
    return sizes


def parse_size(value: object) -> tuple[int, int] | None:
    """归一成 ``(宽, 高)``；不合规返回 None。"""
    sizes = parse_sizes(value, expected=2)
    return (sizes[0], sizes[1]) if sizes else None


def load_sizes(key: str, *, expected: int) -> list[int] | None:
    return parse_sizes(gui_settings().value(key), expected=expected)


def save_sizes(key: str, sizes: list[int]) -> None:
    gui_settings().setValue(key, [int(size) for size in sizes])


def load_size(key: str) -> tuple[int, int] | None:
    return parse_size(gui_settings().value(key))


def save_size(key: str, size: tuple[int, int]) -> None:
    gui_settings().setValue(key, [int(size[0]), int(size[1])])
