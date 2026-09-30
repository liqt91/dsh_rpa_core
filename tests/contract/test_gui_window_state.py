"""窗口状态持久化判据（M49 P1-1）。

判的是「用户调过一次就不用再调」这类状态真的被记住并还原：
主窗几何 + 三栏比例（关窗存、开窗还）、元素编辑对话框尺寸（关窗存、开窗还），
外加一条**回落判据**——坏配置必须退回构造时的默认值，而不是把窗口开成畸形。

配套 ``conftest.py`` 的 autouse 夹具把 QSettings 落到 tmp_path；本文件里的用例因此
不依赖、也不污染书写者的真实配置。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="原生 GUI 为可选能力，需 uv sync --extra gui")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


def _window(catalog, tmp_path):
    from rpa_core.gui.app import MainWindow

    return MainWindow(catalog, workflows_root=tmp_path / "workflows")


def _browser_document() -> dict:
    return {
        "kind": "browser",
        "selector": {"css": "#q", "candidates": [{"kind": "id", "selector": "#q"}]},
        "verifyCount": 1,
        "metadata": {"tag": "input", "role": "searchbox", "url": "https://x/"},
    }


# ---- 纯解析（判据的第一层：不依赖 Qt 窗口） --------------------------------------


def test_parse_sizes_accepts_platform_shapes():
    """QSettings 在不同平台回 list[int] / list[str] / 逗号串，都要能认。"""
    from rpa_core.gui.persist import parse_sizes

    assert parse_sizes([280, 700, 300], expected=3) == [280, 700, 300]
    assert parse_sizes(("280", "700", "300"), expected=3) == [280, 700, 300]
    assert parse_sizes("280, 700, 300", expected=3) == [280, 700, 300]
    assert parse_sizes(["280", "700", "300"], expected=3) == [280, 700, 300]


def test_parse_sizes_rejects_garbage():
    """坏值一律当「没有记录」：个数不对、非数字、非正数都不许放行。"""
    from rpa_core.gui.persist import parse_sizes

    assert parse_sizes(None, expected=3) is None
    assert parse_sizes([280, 700], expected=3) is None
    assert parse_sizes(["a", "b", "c"], expected=3) is None
    assert parse_sizes([280, -700, 300], expected=3) is None
    assert parse_sizes([280, 0, 300], expected=3) is None
    assert parse_sizes("", expected=3) is None
    assert parse_sizes(123, expected=3) is None


def test_parse_size_pairs():
    from rpa_core.gui.persist import parse_size

    assert parse_size([680, 720]) == (680, 720)
    assert parse_size("680 720") == (680, 720)
    assert parse_size([680]) is None
    assert parse_size(None) is None


# ---- 主窗：三栏比例 -------------------------------------------------------------


def test_main_window_restores_saved_splitter_proportions(qapp, catalog, tmp_path):
    """上次拖过的三栏比例，开窗就按它排（而不是回到 280/700/300）。"""
    from rpa_core.gui.app import MainWindow
    from rpa_core.gui.persist import save_sizes

    control = MainWindow(catalog, workflows_root=tmp_path / "w1")
    default_sizes = control._splitter.sizes()

    save_sizes("mainWindow/splitter", [400, 300, 500])
    window = MainWindow(catalog, workflows_root=tmp_path / "w2")
    restored = window._splitter.sizes()

    assert restored != default_sizes, f"没有还原：{restored} 与默认 {default_sizes} 相同"
    total = sum(restored)
    default_ratio = default_sizes[0] / sum(default_sizes)
    saved_ratio = 400 / 1200
    # 三栏各有最小宽度，Qt 会把请求值夹一下 ⇒ 不判精确比例，判「更靠近保存值」
    assert abs(restored[0] / total - saved_ratio) < abs(restored[0] / total - default_ratio)
    assert restored[2] / total > default_sizes[2] / sum(default_sizes)
    window.close()


def test_main_window_falls_back_on_broken_splitter_record(qapp, catalog, tmp_path):
    """坏配置退回默认布局，且不抛异常（宁可出厂布局，也不要开不出窗口）。"""
    from rpa_core.gui.app import MainWindow
    from rpa_core.gui.persist import gui_settings

    gui_settings().setValue("mainWindow/splitter", "这不是尺寸")
    window = MainWindow(catalog, workflows_root=tmp_path / "w")
    control = MainWindow(catalog, workflows_root=tmp_path / "w2")
    assert window._splitter.sizes() == control._splitter.sizes()
    window.close()


def test_main_window_saves_state_on_close(qapp, catalog, tmp_path):
    """关窗写回几何与比例——不写的话「记住尺寸」就只是开窗时的一句空话。"""
    from rpa_core.gui.persist import gui_settings, load_sizes

    window = _window(catalog, tmp_path)
    window.show()
    qapp.processEvents()
    window.resize(1000, 640)
    window._splitter.setSizes([111, 555, 333])
    qapp.processEvents()
    window.close()

    saved = load_sizes("mainWindow/splitter", expected=3)
    assert saved is not None and len(saved) == 3
    assert saved[1] == pytest.approx(555, rel=0.25), saved
    geometry = gui_settings().value("mainWindow/geometry")
    assert geometry is not None, "几何没有落盘"


# ---- 元素编辑对话框尺寸 ---------------------------------------------------------


def test_element_editor_dialog_restores_saved_size(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.gui.persist import save_size

    save_size("elementEditor/size", (900, 620))
    dialog = ElementEditorDialog(_browser_document(), name="q")
    assert (dialog.width(), dialog.height()) == (900, 620)
    dialog.close()


def test_element_editor_dialog_defaults_without_record(qapp):
    """没有记录时保持 M47.3 定的默认尺寸（680×720）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog

    dialog = ElementEditorDialog(_browser_document(), name="q")
    assert (dialog.width(), dialog.height()) == (680, 720)
    dialog.close()


def test_element_editor_dialog_saves_size_on_close(qapp):
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.gui.persist import load_size

    dialog = ElementEditorDialog(_browser_document(), name="q")
    dialog.show()  # 未显示的窗口 close() 不派发关闭事件，尺寸也就无从记录
    qapp.processEvents()
    dialog.resize(960, 700)
    qapp.processEvents()
    dialog.close()
    assert load_size("elementEditor/size") == (960, 700)


def test_element_editor_dialog_saves_size_when_cancelled(qapp):
    """取消/点 X 也要记住尺寸（用户调窗是为了看得清，不是想保存）。"""
    from rpa_core.gui.element_editor import ElementEditorDialog
    from rpa_core.gui.persist import load_size

    dialog = ElementEditorDialog(_browser_document(), name="q")
    dialog.resize(880, 660)
    dialog.reject()
    assert load_size("elementEditor/size") == (880, 660)
