from rpa_core.capture.desktop_agent import _drill_to_leaf


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Fake:
    def __init__(self, name, rect, children=(), handle=0):
        self.name = name
        self.rectangle = rect
        self._children = list(children)
        self.handle = handle

    def children(self):
        return list(self._children)


def _tree():
    # root(window 100x100) ─ pane(同大) ─ label(小文本) + button(另一区域)
    label = _Fake("label", _Rect(10, 10, 30, 24), handle=13)
    button = _Fake("button", _Rect(40, 40, 90, 70), handle=14)
    pane = _Fake("pane", _Rect(0, 0, 100, 100), children=[label, button], handle=12)
    root = _Fake("root", _Rect(0, 0, 100, 100), children=[pane], handle=11)
    return root, pane, label, button


def test_drill_reaches_deepest_leaf_for_small_text():
    root, _pane, label, _button = _tree()
    result = _drill_to_leaf(root, 15, 15)
    assert result is label


def test_drill_stops_at_smallest_containing_when_leaf_misses_point():
    root, pane, _label, _button = _tree()
    result = _drill_to_leaf(root, 50, 50)  # 只在 pane/button 范围，不在 label
    assert result is _button


def test_drill_root_when_no_child_contains():
    root, _pane, _label, _button = _tree()
    result = _drill_to_leaf(root, 150, 150)  # 点在所有子元素之外
    assert result is root


def test_drill_handles_children_errors_gracefully():
    class Broken(_Fake):
        def children(self):
            raise RuntimeError("com busy")

    result = _drill_to_leaf(Broken("root", _Rect(0, 0, 10, 10)), 5, 5)
    assert result.name == "root"


def test_drill_caps_huge_containers():
    # 大容器（如浏览器 Document）超过 max_children 即停，不向下钻
    children = [_Fake(f"c{i}", _Rect(0, 0, 100, 100)) for i in range(300)]
    root = _Fake("document", _Rect(0, 0, 100, 100), children=children)
    result = _drill_to_leaf(root, 50, 50)
    assert result is root


def test_drill_skips_children_with_broken_rect():
    good = _Fake("good", _Rect(5, 5, 20, 20), handle=8)

    class RectErr(_Fake):
        def __init__(self, name, children=(), handle=0):
            self.name = name
            self._children = list(children)
            self.handle = handle

        @property
        def rectangle(self):
            raise RuntimeError("rect error")

    bad = RectErr("bad", handle=9)
    root = _Fake("root", _Rect(0, 0, 100, 100), children=[bad, good])
    result = _drill_to_leaf(root, 10, 10)
    assert result is good
