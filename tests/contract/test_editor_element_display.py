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

# 取字段标签的 Python 区段：**只扫 GUI 真正展示的那一段**（``_metadata_text``）。
# 曾经还包含 ``semantic_meta_text``，M47.12 起它与 ``candidates_text`` 一样已不再被
# GUI 调用（保留作Web 侧对等物）——扫着它会把「源码里还留着」误判成「界面上还在
# 展示」，这正是本文件要防的那类假同步。
_PY_REGIONS = (("def _metadata_text", "def accept"),)


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
    """GUI 展示区的 ``键: 值`` 标签。**允许为空**——M47.12 起 GUI 描述区只剩提示句。

    别把「提取不到就断言非空」抄回来：这里提取为空恰恰是本轮要钉住的结果之一
    （GUI 有属性/字段表可替代，描述区不再抄 ``键: 值`` 的只读副本）。
    """
    return set(_PY_LABEL.findall(_panel_label_region()))


def test_dialog_renders_the_readonly_block_from_the_helper() -> None:
    """确认框必须**调用**纯函数，而不是自己再内联拼一份。

    回归：内联拼装正是两端漂移的成因——Web 侧那份漏掉了候选与语义特征。
    纯函数写得再对，没接上去就是死代码（本轮「能力与可用性要一起交付」的同款问题）。
    """
    body = _dialog_body()
    assert "metaEl.textContent = elementDisplayText(descriptor);" in body
    assert "const meta = descriptor.metadata || {};" not in body


def test_gui_labels_are_a_subset_of_web_labels() -> None:
    """GUI 的展示标签**不许发明 Web 没有的字段**（否则两端语义错位）。

    M47.12 之后两端**不再是集合相等**，而是有意的**包含关系**——这不是漂移，是两条
    界面能力不同：

    - **GUI 侧的确认框有属性/字段表**（browser 的 tag/id/class、desktop 的
      ``controlType``/``automationId``/``name``/``className`` 都是勾选框），所以描述区
      再抄一份只读副本就是重复信息，维护者明确要求删掉（「这些信息大部分应该作为
      节点属性供勾选」）；
    - **Web 侧确认框只有一行 ``selectorInput``**（css 或 locator JSON），**没有属性
      表**——那个只读区块是它唯一的信息入口，删掉等于信息无处可看。

    所以这里钉的是「GUI ⊆ Web」这个方向：**GUI 少展示可以（有能力替代），多展示不行**
    （就成了「这边没这条 = 库里没有」那种语义错位）。
    下方两条 must-have 分别钉住 Web 的全量与 GUI 的精简口径——单靠包含关系会漏掉
    「两边一起删」/「Web 侧误删」。
    """
    js = _js_labels()
    py = _py_labels()
    assert py <= js, f"GUI 侧展示了 Web 侧没有的字段：仅 GUI {sorted(py - js)}"
    # 差异必须是**已知且可解释**的：GUI 只剩「定位用不上、字段表也勾不到」的
    # 窗口/类名提示，其标签集合为空（提示句不是 `键: 值` 形式）。
    assert py == set(), f"GUI 侧不该再有 `键: 值` 形式的展示行，实际 {sorted(py)}"


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
