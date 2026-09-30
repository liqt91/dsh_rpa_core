"""GUI 颜色 token 判据（M49 P0-1）。

铁律：``src/rpa_core/gui/`` 下**只有 theme.py** 可以出现十六进制颜色字面量，
其余模块一律引用 :mod:`rpa_core.gui.theme` 的语义常量。

为什么用 AST 而不是文本 grep：色值既可能出现在 ``"color: #fff;"`` 这类字面量里，
也可能出现在 f-string 的常量片段里，而注释/文档串里的 ``#`` 是纯文本——
文本 grep 会把注释当违规（假红），AST 扫 :class:`ast.Constant` 恰好只覆盖「真的会
被渲染的字符串」。已知盲区：``rgb(26,127,55)`` 这类写法扫不到，出现时把正则补上。

判据成对（照项目惯例）：①「两侧相等」——除 theme.py 外无任何色值字面量；
②「must-have 名单」——theme.py 必须仍然导出全部语义 token。只做①的话，
把 token 定义连同引用一起删掉照样绿。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

GUI_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui"
THEME_FILE = "theme.py"
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\b")

# must-have：语义 token 名单（改名/删名即红，防止「两边一起删」）
REQUIRED_TOKENS = (
    "SUCCESS",
    "DANGER",
    "WARNING",
    "CAUTION",
    "INFO",
    "ACCENT",
    "TEXT",
    "TEXT_HEADING",
    "TEXT_SECONDARY",
    "TEXT_MUTED",
    "TEXT_FAINT",
    "NEUTRAL",
    "FALLBACK",
    "BORDER",
    "BORDER_HOVER",
    "BORDER_SOFT",
    "GRIP",
    "SURFACE",
    "SURFACE_HOVER",
    "SURFACE_SELECTED",
    "SURFACE_SUNKEN",
    "SURFACE_GROUP",
    "SURFACE_GROUP_HOVER",
    "SURFACE_HIGHLIGHT",
    "FONT_MONO",
    "OVERLAY_BORDER",
    "OVERLAY_SHADOW",
    "DEPTH_COLORS",
)


def _string_constants(path: pathlib.Path) -> list[tuple[int, str]]:
    """文件里所有字符串常量（含 f-string 的常量片段）及其行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.value))
    return found


def _gui_modules() -> list[pathlib.Path]:
    return sorted(p for p in GUI_DIR.glob("*.py") if p.name != THEME_FILE)


def test_gui_modules_do_not_hardcode_hex_colors() -> None:
    """除 theme.py 外，GUI 模块不得出现十六进制色值字面量。"""
    offenders: list[str] = []
    for path in _gui_modules():
        for lineno, text in _string_constants(path):
            for match in HEX_COLOR_RE.findall(text):
                offenders.append(f"{path.name}:{lineno}: {match}")
    assert not offenders, (
        "GUI 颜色必须走 rpa_core.gui.theme 的语义 token，以下位置写死了色值：\n"
        + "\n".join(offenders)
    )


def test_theme_defines_required_tokens() -> None:
    """theme.py 必须导出全部语义 token（防止「定义与引用一起删」的假绿灯）。"""
    theme_path = GUI_DIR / THEME_FILE
    assert theme_path.exists(), "src/rpa_core/gui/theme.py 必须存在"
    namespace: dict[str, object] = {}
    exec(compile(theme_path.read_text(encoding="utf-8"), THEME_FILE, "exec"), namespace)
    missing = [name for name in REQUIRED_TOKENS if name not in namespace]
    assert not missing, f"theme.py 缺少语义 token：{missing}"


def test_theme_tokens_are_valid_colors() -> None:
    """每个颜色 token 要么是 #RRGGBB，要么是 QColor 可用的 rgba 元组。"""
    theme_path = GUI_DIR / THEME_FILE
    namespace: dict[str, object] = {}
    exec(compile(theme_path.read_text(encoding="utf-8"), THEME_FILE, "exec"), namespace)
    for name in REQUIRED_TOKENS:
        value = namespace[name]
        if name in {"FONT_MONO", "DEPTH_COLORS"}:
            continue
        if isinstance(value, tuple):
            assert len(value) in {3, 4} and all(
                isinstance(channel, int) and 0 <= channel <= 255 for channel in value
            ), f"{name} 不是合法的 rgba 元组：{value!r}"
        else:
            assert HEX_COLOR_RE.fullmatch(str(value)), f"{name} 不是 #RRGGBB：{value!r}"


@pytest.mark.parametrize("name", ["DEPTH_COLORS"])
def test_derived_collections_reference_tokens(name: str) -> None:
    """派生集合的元素必须来自已定义的 token，而不是另一份色值字面量。"""
    theme_path = GUI_DIR / THEME_FILE
    namespace: dict[str, object] = {}
    exec(compile(theme_path.read_text(encoding="utf-8"), THEME_FILE, "exec"), namespace)
    colors = namespace[name]
    assert isinstance(colors, tuple) and colors, f"{name} 应是非空元组"
    known = {
        value
        for key, value in namespace.items()
        if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)
    }
    assert set(colors) <= known, f"{name} 含未定义色值：{set(colors) - known}"
