"""GUI 字号口径判据（M49 P3，即 M31 审计里的**字号**切片）。

改之前两套口径混用：基准字体走 pt（``QFont(family, 9)``），派生字号也走 pt
（``option.font.pointSizeF() - 0.5``），而 QSS 里写死 px（``font-size: 14px``）。
两套只在 96dpi 下恰好对齐；更要命的是——**基准字号一旦是像素，``pointSizeF()``
返回 -1**，派生字号就从「小一号」变成「没有字号」，Qt 静默回落到系统默认大小。

本文件判四件事（照项目惯例，行为与接线各一层）：

1. ``fonts`` 的 token 都是正的整数像素，且派生函数在「基准没有有效像素」时回落；
2. 除 ``fonts.py`` 外，GUI 模块不再调用 pt 口径的 API（``pointSize/pointSizeF/
   setPointSize/setPointSizeF``）——这是「口径统一」真正的那条腿；
3. QSS 里的 ``font-size`` 不再写死数字，只能由 token 拼出来（并给 must-have 名单，
   防「把字号声明整段删掉」的假绿灯）；
4. ``apply_theme`` 的基准字体确实走 ``fonts.base_font``（接线层，纯函数正确 ≠ 用上了）。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")

GUI_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui"
FONTS_FILE = "fonts.py"

# pt 口径的 API：口径统一后除 fonts.py 外一律不得再出现
POINT_API = {"pointSize", "pointSizeF", "setPointSize", "setPointSizeF"}
# QSS 里写死的字号（"font-size: 12px"）——token 拼出来的是 f-string，常量片段里没有数字
LITERAL_PX_RE = re.compile(r"font-size\s*:\s*\d")
# must-have：这些模块必须仍然设有 QSS 字号（且必须是 token 形式）
QSS_FONT_SIZE_MODULES = ("app.py", "command_matrix.py", "home.py", "splash.py")
TOKEN_QSS_RE = re.compile(r"font-size:\s*\{fonts\.\w+\}px")


def _module_paths() -> list[pathlib.Path]:
    return sorted(p for p in GUI_DIR.glob("*.py") if p.name != FONTS_FILE)


def _module_names_of_point_api() -> list[str]:
    offenders: list[str] = []
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in POINT_API
                and isinstance(node.ctx, ast.Load)
            ):
                offenders.append(f"{path.name}:{node.lineno}: {node.attr}")
    return offenders


# ---- 纯函数层 ---------------------------------------------------------------


def test_font_tokens_are_positive_pixel_integers() -> None:
    from rpa_core.gui import fonts

    required = (
        "BASE_PX", "SUB_PX", "MONO_PX", "HEADING_PX", "TITLE_PX", "CODE_PX", "MIN_PX",
    )
    for name in required:
        value = getattr(fonts, name, None)
        assert isinstance(value, int), f"fonts.{name} 必须是整数像素，实际 {value!r}"
        assert value > 0, f"fonts.{name} 必须是正的像素值，实际 {value}"
    assert fonts.MONO_FAMILY == "Consolas"


def test_base_font_is_pixel_sized() -> None:
    """基准字体必须是像素口径（pt 口径的 ``pointSizeF()`` 会掩盖「没设字号」）。"""
    from rpa_core.gui import fonts

    font = fonts.base_font()
    assert font.pixelSize() == fonts.BASE_PX
    assert font.pointSizeF() == -1, "基准字号设了 pt 口径，派生字号会退化成 -1"


def test_scaled_uses_pixel_arithmetic() -> None:
    from rpa_core.gui import fonts

    base = fonts.base_font("Microsoft YaHei")
    assert fonts.scaled(base, delta=-2).pixelSize() == fonts.BASE_PX - 2
    assert fonts.scaled(base, delta=+2).pixelSize() == fonts.BASE_PX + 2


def test_scaled_falls_back_when_base_has_no_pixel_size() -> None:
    """基准字体只有 pt 字号（或压根没设）时，派生字号必须回落，不能变成「无字号」。"""
    from PySide6.QtGui import QFont

    from rpa_core.gui import fonts

    pt_only = QFont("Microsoft YaHei")
    pt_only.setPointSize(9)
    assert pt_only.pixelSize() == -1  # 夹具自身的前提，避免判据落空

    derived = fonts.scaled(pt_only, delta=-2)
    assert derived.pixelSize() == fonts.BASE_PX - 2


def test_scaled_respects_minimum() -> None:
    from rpa_core.gui import fonts

    tiny = fonts.base_font()
    tiny.setPixelSize(fonts.MIN_PX - 4)
    assert fonts.scaled(tiny, delta=-5).pixelSize() == fonts.MIN_PX


def test_mono_font_is_pixel_sized_and_monospace() -> None:
    from rpa_core.gui import fonts

    font = fonts.mono_font()
    assert font.family() == fonts.MONO_FAMILY
    assert font.pixelSize() == fonts.MONO_PX


# ---- 接线层 ----------------------------------------------------------------


def test_no_point_size_api_outside_fonts_module() -> None:
    """除 fonts.py 外不得再用 pt 口径 API——这是「两套口径」的根。"""
    offenders = _module_names_of_point_api()
    assert not offenders, (
        "GUI 字号必须走 rpa_core.gui.fonts 的像素口径，以下位置仍在用 pt 口径：\n"
        + "\n".join(offenders)
    )


def test_qss_font_size_is_never_a_literal_number() -> None:
    offenders: list[str] = []
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if LITERAL_PX_RE.search(node.value):
                    offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert not offenders, (
        "QSS 字号必须由 fonts 的 token 拼出来，以下位置写死了数字：\n"
        + "\n".join(offenders)
    )


def test_modules_still_set_qss_font_size_from_tokens() -> None:
    """must-have：这些模块必须有 token 形式的 ``font-size``（防「整段删掉」）。"""
    for name in QSS_FONT_SIZE_MODULES:
        source = (GUI_DIR / name).read_text(encoding="utf-8")
        assert TOKEN_QSS_RE.search(source), f"{name} 缺少 token 形式的 QSS 字号"


def test_apply_theme_sets_pixel_base_font() -> None:
    """接线：``apply_theme`` 的基准字体来自 fonts.base_font（不是 QFont(family, 9)）。"""
    source = (GUI_DIR / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    apply_theme = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "apply_theme"
    )
    body = ast.get_source_segment(source, apply_theme) or ""
    assert "fonts.base_font(" in body, "apply_theme 未使用 fonts.base_font 设置基准字体"
