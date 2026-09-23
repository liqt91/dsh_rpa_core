"""编辑器「元素确认框只读区块」：两端同口径（Web `static/app.js` ↔ GUI `gui/element_panel.py`）。

同一份元素文档在两个编辑器里必须显示同一组事实。2026-09-23 的元素捕获链路调研发现
**一端显示了、另一端没显示**：`selector.candidates`（备选定位 + 捕获时命中数）与 browser 的
语义特征，此前**两端都没展示**；GUI 侧补上后 Web 侧仍没有 —— 这种不一致比「两边都没显示」
更坏：用户会以为「这边没有这条信息 = 库里没有」，实际只是没画出来。

分工（不要互相替代）：

- **渲染行为**由 node 门禁 ``scripts/check_element_display_helpers.mjs`` 用真实捕获载荷验——
  Python 读源码只能证明「写了这行字」，证明不了「渲染出哪几行」；
- **本文件**管 Python 测得到的两件事：① 接线（纯函数写得再好，没接上去也是死代码）；
  ② 两端**字段口径逐字相同**（缺一个就红）。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "src" / "rpa_core" / "devserver" / "static" / "app.js"
ELEMENT_PANEL = ROOT / "src" / "rpa_core" / "gui" / "element_panel.py"

_HELPERS_START = "// [element-display-helpers:start]"
_HELPERS_END = "// [element-display-helpers:end]"

# ``tag: ${…}``（JS 模板串）/ ``f"tag: {…}"``（Python f-string）里的字段标签
_JS_LABEL = re.compile(r"`([A-Za-z][A-Za-z0-9]*): \$\{")
_PY_LABEL = re.compile(r'f"([A-Za-z][A-Za-z0-9]*): \{')

# 取字段标签的 Python 区段：只在这两个函数里数，避免文件里别处的 f-string 误入
_PY_REGIONS = (
    ("def semantic_meta_text", "def candidates_text"),
    ("def _metadata_text", "def accept"),
)


def _app_js_text() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _helpers_slice() -> str:
    """app.js 的展示纯函数区（node 门禁切的是同一段，锚点必须一致）。"""
    text = _app_js_text()
    start = text.find(_HELPERS_START)
    end = text.find(_HELPERS_END)
    assert start >= 0 and end > start, "app.js 缺 element-display-helpers 锚点"
    return text[start + len(_HELPERS_START) : end]


def _dialog_body() -> str:
    """``openElementDialog`` 的函数体（接线断言的切片区）。"""
    text = _app_js_text()
    start = text.index("function openElementDialog(")
    end = text.index("async function editElement(")
    assert end > start
    return text[start:end]


def _element_panel_text() -> str:
    return ELEMENT_PANEL.read_text(encoding="utf-8")


def _panel_label_region() -> str:
    text = _element_panel_text()
    chunks: list[str] = []
    for head, tail in _PY_REGIONS:
        start = text.index(head)
        end = text.index(tail, start)
        chunks.append(text[start:end])
    return "\n".join(chunks)


def _js_labels() -> set[str]:
    labels = set(_JS_LABEL.findall(_helpers_slice()))
    assert labels, "未从 app.js 提取到字段标签（锚点或模板串格式变了？）"
    return labels


def _py_labels() -> set[str]:
    labels = set(_PY_LABEL.findall(_panel_label_region()))
    assert labels, "未从 element_panel.py 提取到字段标签（函数被改名了？）"
    return labels


def test_dialog_renders_the_readonly_block_from_the_helper() -> None:
    """确认框必须**调用**纯函数，而不是自己再内联拼一份。

    回归：内联拼装正是两端漂移的成因——Web 侧那份漏掉了候选与语义特征。
    纯函数写得再对，没接上去就是死代码（本轮「能力与可用性要一起交付」的同款问题）。
    """
    body = _dialog_body()
    assert "metaEl.textContent = elementDisplayText(descriptor);" in body
    assert "const meta = descriptor.metadata || {};" not in body


def test_both_surfaces_show_the_same_field_labels() -> None:
    """两端展示的字段标签集合必须逐字相同（缺一个就红）。

    先比**集合相等**抓两端漂移，再用下面的 must-have 抓「两边一起被删」——
    只比相等的话，同时删掉两端同一个字段也能过。
    """
    js = _js_labels()
    py = _py_labels()
    assert js == py, (
        f"两端展示字段不一致：仅 Web {sorted(js - py)}；仅 GUI {sorted(py - js)}"
    )


def test_display_covers_the_facts_that_were_missing() -> None:
    """钉住本轮补上的那几项：两边一起删也必须红。

    ``className`` / ``name`` 是桌面侧定位**无窗口文本控件**（ListBox/ComboBox）的依据，
    ``className`` 同时是 ``classNameRe`` 正则的输入；``accessibleName`` / ``label`` /
    ``containerText`` 是候选命中多个时人工消歧最有用的信息。
    """
    js = _js_labels()
    must_have = {"className", "name", "accessibleName", "label", "containerText", "role"}
    assert must_have <= js, f"展示面缺了这些字段：{sorted(must_have - js)}"


def test_both_surfaces_render_the_candidate_block() -> None:
    """备选定位是运行期自愈的依据，两端都要露出来（有数据、无界面 = 用户不知道它存在）。"""
    assert "备选定位" in _helpers_slice()
    assert "备选定位" in _panel_label_region() or "备选定位" in _element_panel_text()
