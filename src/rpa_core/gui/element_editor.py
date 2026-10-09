"""元素编辑器（M39 ③-1，离线版）。

影刀的元素编辑器活在**捕获会话里**：左侧 DOM 节点树、右侧属性勾选、活体校验。我们没有
活页面就做不出节点树与活体验证（见 ``.harness/tasks/M39-element-editor.md`` 的 ③-2）。
但用户真正卡住的那一步并不需要活页面：

    **不写 CSS、不写 JSON，也能选出一个定位方案。**

v1 只做这件事，全部基于**已捕获的数据**：

1. **浏览器元素：影刀式两页签**（M47.11）——「预览」是**页面截图 + 元素红框**
   （扩展侧 ``chrome.tabs.captureVisibleTab`` 现场拍，框由首个命中的视口 rect
   换算而来）；「精准定位」是节点树 + 属性表并排。底部「默认选择器 / XPath」单选与
   「锚点 + 添加」按维护者要求**摆出来但置灰**（XPath 与锚点都还没有运行期消费方，
   做成能点的就是假功能，见 M51）。
2. **桌面 locator 字段化** —— 勾选框代替手写 JSON（勾上写进 locator、取消即移除）；
   字段集**按 backend 分**，见下。
3. **就地结构校验** —— 每次改动都拿**模型**判一次，错误显示在对话框内。界面**不另立
   一套规则**：判据的权威只有 ``DesktopLocator`` / ``selector_errors``，两套规则必然漂移。

**备选定位（候选）界面已移除**（M47.11，维护者「备选移除吧」，并实测「几次捕捉都没看到
有备选定位」）。但 ``selector.candidates`` 在 ``result_document`` 里**原样带回**——运行期
自愈（``executors.browser._element_candidates`` 按失败 selector 反查取用）仍读它，
删界面不等于删数据。纯函数 ``promote_candidate`` / ``promotable`` 一并退役（无调用方即死代码）。

**字段集按 backend 分，这不是排版偏好**：实测两个执行器消费的 locator 字段完全不同
（``grep -o 'locator\\.[a-z_]*'`` 逐点核过）：

===================  ================  ================
locator 字段          uia ``_find``     win32 ``_find``
===================  ================  ================
controlType          ✅                ✗
automationId         ✅                ✗
name                 ✅（映射 title）  ✗
title                ✗                 ✅
className            ✗                 ✅
classNameRe          ✗                 ✅
controlId            ✗                 ✅
foundIndex           ✗                 ✅
menuPath             ✗                 ✗
handle               ✗                 ✗
===================  ================  ================

在界面上提供**对面后端的字段**，等于让用户在生产一个「写了没人读」的静默死字段——
正是本项目最忌讳的一类错误。``menuPath`` / ``handle`` 两边都不消费（前者只作为
``desktop_win32.menuSelect`` **命令输入**被读，从不来自 locator），所以一并不提供，
并把「locator 里有死字段」这件事在界面里如实提示（见 ``inert_locator_keys``）。

**刻意不做**（都有具体理由，不是漏掉）：

- **重命名**：元素名是身份。改名走捕获/入库那条已有的「同名覆盖保护」路径，编辑器再开
  一条会多出一份语义要同步。
- **``handle``**：运行期值（每次启动都变），摆出来只会诱导用户写出下次必定失效的 locator
  （同确认框不展示 ``windowHandle``）。
- **活体校验**：见 ③-2。**注意**：活体校验的能力其实已经存在——内容脚本的
  动作路径里就有 ``document.querySelectorAll(selector).length`` 与遮挡/可见性预检，
  缺的不是能力而是**通道**：GUI 的 ``_capture_element`` 在 ``work()`` 的 finally 里
  ``session.close()``，即对话框弹出前捕获会话已撤防并关掉 bridge 通道。所以 ③-2 的
  第一件事是定「会话在对话框期间保持 arm」的生命周期改造，不是写查询代码。
- **DOM 节点树**：曾列在这里，M44 S5 起有了**离线版**——基于捕获回传的
  ``selector.path``（祖先链）逐级勾选、按 ``fragment`` join 拼回主 css。它与影刀的
  差距只剩「实时页面树」（重开页面再扫一遍 DOM），勾选-重拼这一步不再缺。
"""

from __future__ import annotations

import base64
import binascii
import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rpa_core.gui.persist import load_size, save_size
from rpa_core.gui.theme import (
    DANGER,
    SUCCESS,
    TEXT_SECONDARY,
    WARNING,
)
from rpa_core.model.desktop import LOCATOR_STEP_KEYS, prune_locator_steps


class SelectorEdit(QPlainTextEdit):
    """主选择器输入框：**多行 + 随内容自适应高度**（M47.10）。

    为什么不是 ``QLineEdit``：主选择器是祖先链拼出来的（``body > div.wrap > button.ok
    :nth-of-type(2) > span``），在 560px 宽的对话框里一行装不下，``QLineEdit`` 会把尾巴
    截掉，用户没法一眼看全自己到底定位到了哪一层（维护者实测反馈「主选择器的框太小了」）。
    换多行后长选择器换行显示、可整段读，同时不再需要横向滚动。

    **对外 API 与 ``QLineEdit`` 同名**（``text()`` / ``setText()`` / ``textChanged``）：
    编辑区里读/写主选择器的调用点有二十来处，全部改签名等于把一次纯排版改动扩散成
    大面积重构。这里刻意保留同名三件套，让替换是**就地**的——落盘口径仍只有一个
    ``self.css_edit``，不新增第二条路径。

    **不遮蔽原生 ``textChanged``**（踩过的坑）：初版写了 ``textChanged = Signal(str)``
    盖住 ``QPlainTextEdit.textChanged()``，想借它对外提供 ``QLineEdit`` 的
    ``textChanged(str)`` 语义；结果 ``super().textChanged`` 在 Python MRO 下仍解析到
    **子类那个从未 emit 的**信号，``.connect`` 连上去永远不触发——``setText`` 之后
    界面毫无反应（``test_editor_browser_blocks_blank_css`` 直接红）。
    ``QPlainTextEdit.textChanged`` 还是 ``Signal`` descriptor，想直接 ``.connect``
    原生信号会 ``AttributeError``。

    正确做法：**让原生信号保持可用**（调用方连到它就是 ``QLineEdit.textChanged``
    的等价能力——无参信号 + 读 ``.text()``），高度重排另外挂在
    ``document().contentsChanged`` 上（编辑器唯一的「文本变了」真源，``setPlainText``
    与键入都会触发）。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlainText(text)
        # 高度自适应：按当前行数算（1~4 行），保持「一行时紧凑、多行时放开」。
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sync_height()
        # 文本变化的真源：document 的 contentsChanged（setPlainText / 键入都走它）。
        # 不碰原生 textChanged——它保持原样供调用方连接。
        self.document().contentsChanged.connect(self._sync_height)

    def _sync_height(self) -> None:
        """按当前文本的**显示行数**（含自动换行折行）把高度钉在 1~4 行。

        显示行数按「每行文本宽度 ÷ 可用宽度」估：``QLineEdit`` 时代一行就够，
        换多行后长选择器会被 WidgetWidth 折行——用字符数估算比
        ``document().size().height()`` 可靠（后者在 ``textChanged`` 同步时刻还没重排）。
        """
        metrics = QFontMetrics(self.font())
        line = metrics.lineSpacing() or 16
        margins = self.contentsMargins().top() + self.contentsMargins().bottom()
        available = max(1, self.viewport().width() - margins)
        displayed = 0
        for raw_line in self.text().splitlines() or [""]:
            width = metrics.horizontalAdvance(raw_line) or 1
            displayed += max(1, -(-width // available))  # 向上取整
        rows = max(1, min(4, displayed or 1))
        self.setFixedHeight(int(line * rows + margins + 12))

    def text(self) -> str:  # noqa: A003 - 与 QLineEdit 同名，替换才是就地的
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802 - 与 QLineEdit 同名
        self.setPlainText(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        # 宽度变了，折行数就变：高度必须跟着重算（否则拉宽对话框后仍停在多行高度）
        self._sync_height()


# 每个 backend **真正被消费**的 locator 字段：``(键, 值类型, 说明)``。
# 值类型只有 "text" / "int"；顺序即界面顺序。
#
# **只放标量字段**（值能摊成一个字符串）。结构字段（`path` 是层级列表、`anchor` 是嵌套
# locator）走专门控件，见 `STRUCTURED_LOCATOR_KEYS`——它们塞不进「一个输入框一个值」的
# 表，硬塞会让 `split_locator` / `compose_locator` 这对**字符串 ↔ 值**的管道把它压成
# repr 字符串（写回模型必炸）或干脆丢掉（静默数据丢失）。
LOCATOR_FIELDS_BY_BACKEND: dict[str, tuple[tuple[str, str, str], ...]] = {
    "uia": (
        ("controlType", "text", "控件类型，如 Button / Edit / List"),
        ("automationId", "text", "最稳，等同网页的 id"),
        ("name", "text", "控件显示名（执行器按 title 匹配）"),
        # D2：控件级匹配方式。`exact`（默认，等价于不给）/ `contains` / `regex`。
        ("matchMode", "text", "匹配方式：exact（默认）/ contains / regex"),
    ),
    "win32": (
        ("title", "text", "窗口文本（等值，已支持 matchMode）"),
        ("className", "text", "类名等值匹配（与 classNameRe 互斥；不参与 matchMode）"),
        ("classNameRe", "text", "类名正则匹配（与 className 互斥）"),
        ("controlId", "int", "控件序号——**每次进程启动都变**，只适合短期脚本"),
        ("foundIndex", "int", "命中多个时取第几个（从 0 开始）"),
        ("matchMode", "text", "匹配方式：exact（默认）/ contains / regex（作用于 title）"),
    ),
}

# 由**专门控件**承载的 locator 字段（值不是标量，不进上面的表）。
# 契约测试（`test_field_tables_match_what_executors_actually_read`）把
# 「标量字段表 ∪ 本集合」与执行器实际读取面比对——两边必须逐字一致。
STRUCTURED_LOCATOR_KEYS: tuple[str, ...] = ("path", "anchor")

BACKENDS: tuple[str, ...] = ("uia", "win32")


def declared_locator_keys() -> tuple[str, ...]:
    """``DesktopLocator`` **声明过**的全部键（别名形式，含未提供的 menuPath / handle）。

    从模型取而不是手抄一份常量：模型加了字段，死字段提示自动覆盖它；手抄的清单
    必然在某个里程碑后过期，而过期的后果是「界面默默放过一个死字段」。
    """
    from rpa_core.model.desktop import DesktopLocator

    return tuple(
        sorted(
            field.alias or name
            for name, field in DesktopLocator.model_fields.items()
        )
    )


ALL_FIELD_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        key
        for fields in LOCATOR_FIELDS_BY_BACKEND.values()
        for key, _kind, _hint in fields
    )
)



class LocatorFieldError(ValueError):
    """字段值本身取不出来（如 controlId / foundIndex 不是整数）。"""


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """元素文档可能被手工改成任意形状，取字段前统一收口（同 element_panel）。"""
    return value if isinstance(value, dict) else {}


def field_kind(key: str) -> str:
    """字段的值类型（``text`` / ``int``）。"""
    for fields in LOCATOR_FIELDS_BY_BACKEND.values():
        for name, kind, _hint in fields:
            if name == key:
                return kind
    return "text"


def fields_for(backend: str) -> tuple[tuple[str, str, str], ...]:
    """该 backend 的字段表；未知 backend 退化成空（校验会另外报出 backend 非法）。"""
    return LOCATOR_FIELDS_BY_BACKEND.get(backend, ())


def field_hint(key: str) -> str:
    """字段的说明文案（用于输入框占位符）。"""
    for fields in LOCATOR_FIELDS_BY_BACKEND.values():
        for name, _kind, hint in fields:
            if name == key:
                return hint
    return ""


def inert_locator_keys(locator: dict[str, Any]) -> list[str]:
    """locator 里**当前后端不消费**的字段（含两边都不消费的 menuPath / handle）。

    这些字段存在也不会报错（模型只知道键合法），但执行器永远读不到——是静默死字段。
    编辑器如实提示，让用户知道保存会把它们去掉，而不是悄悄改他的文件。

    **结构字段（path / anchor）不算死字段**：它们由专门控件承载、被执行器真读，
    只是不进「一个输入框一个值」的表。把它们报成「不消费」正好说反了。
    """
    known = {key for key, _kind, _hint in fields_for(str(locator.get("backend")))}
    known |= set(STRUCTURED_LOCATOR_KEYS)
    declared = set(declared_locator_keys())
    return sorted(
        key
        for key in locator
        if key != "backend" and key in declared and key not in known
    )


def structured_locator_values(locator: dict[str, Any]) -> dict[str, Any]:
    """取出 locator 里的结构字段（原样，不做字符串化）。

    由专门控件负责编辑；`compose_locator` 用 ``structured`` 参数把它们带回组装结果，
    避免「编辑器点一次确定就把捕获回传的祖先链/锚点丢掉」这种**静默数据丢失**。
    """
    return {
        key: locator[key]
        for key in STRUCTURED_LOCATOR_KEYS
        if locator.get(key) is not None
    }


def compose_locator(
    backend: str,
    values: dict[str, str],
    *,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按「勾选的字段」组装 locator：**值为空即不出现**那个键。

    界面用「值空 = 不勾」表达，所以组装层不需要单独的勾选状态——少一个可能与界面
    不同步的状态源。只组装该 backend 的字段表内的字段（杜绝跨后端死字段）。

    ``structured`` 是结构字段（path / anchor）的**原样透传**：它们不经过字符串管道，
    但必须原封带回——否则用户在编辑器里点一次确定，捕获回传的祖先链就没了。
    """
    locator: dict[str, Any] = {"backend": backend}
    for key, kind, _hint in fields_for(backend):
        raw = (values.get(key) or "").strip()
        if not raw:
            continue
        if kind == "int":
            try:
                locator[key] = int(raw)
            except ValueError:
                raise LocatorFieldError(
                    f"{key} 需要整数，收到 {raw!r}"
                ) from None
            continue
        locator[key] = raw
    for key, value in (structured or {}).items():
        if key in STRUCTURED_LOCATOR_KEYS and value is not None:
            locator[key] = value
    return locator


def split_locator(locator: dict[str, Any]) -> dict[str, str]:
    """把 locator 摊成界面用的字符串值（只取该 backend 的字段）。"""
    values: dict[str, str] = {}
    for key, _kind, _hint in fields_for(str(locator.get("backend"))):
        if key not in locator or locator[key] is None:
            continue
        values[key] = str(locator[key])
    return values


# 元素编辑对话框尺寸：默认够宽（节点树 + 属性表），用户调过则沿用（M49 P1-1）
_DIALOG_SIZE_KEY = "elementEditor/size"
# M47.12 加宽：维护者「整个捕获确认窗口可以宽一点，现在节点路径和属性太挤了」。
# 680→860 是**默认**尺寸；下方 :func:`_open_wide_enough` 还会把**已保存的旧窄尺寸**
# 顶到这个下限——否则用户上一次存过 560 宽，下次打开仍是窄的（持久化会盖过默认值）。
_DIALOG_MIN_WIDTH = 860
_DIALOG_DEFAULT_SIZE = (860, 760)


def _open_wide_enough(saved: tuple[int, int] | None) -> tuple[int, int]:
    """取本次开窗尺寸：已保存的尺寸，但**宽度不低于** ``_DIALOG_MIN_WIDTH``。

    为什么要顶宽而不是直接用默认：持久化是「用户调过就沿用」，可那条规则在用户
    尚未见过宽版之前会一直生效——M47.11 的 680 就是这么被继承下来的。高度不设下限：
    矮一点只是少看几行，宽了才是「挤」。
    """
    width, height = saved or _DIALOG_DEFAULT_SIZE
    return (max(int(width), _DIALOG_MIN_WIDTH), int(height))

# 模型报错 → 界面中文。**键是模型原文的稳定子串**；`test_gui_element_editor.py` 会逐条
# 触发这些规则并断言译文命中，所以模型改了措辞就会红（不会静默退回英文原文）。
_LOCATOR_MESSAGE_MAP: tuple[tuple[str, str], ...] = (
    (
        "backend must be uia or win32",
        "backend 只能是 uia 或 win32",
    ),
    (
        "requires at least one UIA identity field",
        "uia 定位至少要勾一个身份字段（controlType / automationId / name）",
    ),
    (
        "requires at least one Win32 identity field",
        "win32 定位至少要勾一个身份字段（title / className / classNameRe / controlId）",
    ),
    (
        "className and classNameRe are mutually exclusive",
        "className（等值）与 classNameRe（正则）互斥，只能留一个",
    ),
    (
        "menuPath cannot be empty",
        "menuPath 不能为空",
    ),
)


def translate_locator_message(message: str) -> str:
    """模型报错译成界面中文；译不出就**原样返回**（不吞、不编）。"""
    for needle, chinese in _LOCATOR_MESSAGE_MAP:
        if needle in message:
            return chinese
    return message


def locator_problems(locator: dict[str, Any]) -> list[str]:
    """用模型判定 locator 是否可用。空列表 = 可用。

    这是「改 → 校验 → 再改」回路里的**校验**一半：判据来自 ``DesktopLocator``，
    与执行器、``selector_errors`` 完全同源。
    """
    from rpa_core.model.desktop import DesktopLocator

    try:
        DesktopLocator.model_validate(locator)
    except ValidationError as exc:
        return [
            translate_locator_message(str(error.get("msg", error)))
            for error in exc.errors()
        ]
    return []


def css_problems(css: str) -> list[str]:
    """浏览器主选择器的离线可判部分：非空。命中数要活页面，归 ③-2。"""
    if not css.strip():
        return ["主选择器（css）不能为空"]
    return []


# ---------------------------------------------------------------------------
# 默认元素名（A3：三端一致生成器）
# ---------------------------------------------------------------------------

_NON_NAME_CHARS = re.compile(r"[^a-z0-9_]")


def normalize_element_hint(hint: str | None) -> str:
    """把「元素种类提示」规整成可拼进名字的词干。

    - 小写化；去掉结尾的 ``control``（UIA 的 CurrentControlType 形如
      ``ButtonControl``，而界面上按钮的真实种类是 ``Button``）；
    - 只留 ``[a-z0-9_]``（tag / controlType 里可能出现 ``-`` 等 css 友好但
      名字不友好的字符）；空了退回 ``element``。
    """
    cleaned = re.sub(r"control$", "", str(hint or "").lower())
    cleaned = _NON_NAME_CHARS.sub("", cleaned)
    return cleaned or "element"


def suggest_element_name(existing: Iterable[str], hint: str | None = None) -> str:
    """给一个未被占用的默认元素名：``el_{种类}``，撞了就 ``_2``、``_3``…。

    GUI（``app.py``）与 Web（``app.js`` 的 ``suggestElementName``）共用同一套
    **方案**（本体是零构建双端，没法真共享代码）——两侧实现由同一份用例表钉住：
    ``tests/contract/data/element_name_cases.json``，Python 侧本文件、JS 侧
    ``scripts/check_element_name.mjs`` 各自跑一遍。改这里必须同步改 JS，反之亦然
    （改完跑两侧判据，不一致会红）。
    """
    taken = set(existing)
    base = f"el_{normalize_element_hint(hint)}"
    if base not in taken:
        return base
    ordinal = 2
    while f"{base}_{ordinal}" in taken:
        ordinal += 1
    return f"{base}_{ordinal}"


# ---------------------------------------------------------------------------
# Web 属性表（A1）：逐属性勾选 + 等于/包含，编译回一层 fragment
# ---------------------------------------------------------------------------

ATTR_EQUALS = "equals"
ATTR_CONTAINS = "contains"


def _escape_css_attr(value: str) -> str:
    """CSS 属性选择器值的转义（与 content.js 的 cssEscapeAttr 同口径）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def attribute_rows(entry: dict[str, Any], fragment: str) -> list[dict[str, Any]]:
    """把一层的捕获属性展开成属性表行，初态按**当前 fragment** 反推。

    行形状：``{attr, value, mode, checked, locked}``。
    - ``tag`` 行：locked（不勾 tag 的层不指向任何东西），无匹配方式；
    - ``id`` 行：捕获片段以 ``#id`` 代表该层（content.js 口径：带 id 即终止上溯），
      所以 checked = ``#id`` 出现在 fragment 里；
    - 每个 class 一行：捕获只取**首类**进 fragment，其余类默认不勾——这正是
      属性表相对节点树新增的颗粒度（影刀：class 可逐个勾、可改「包含」）；
    - ``nth-of-type`` 行：位置信息，无匹配方式，勾选 = 保留。

    匹配方式初值恒为「等于」（与捕获口径一致），「包含」是用户显式选择的产物。
    """
    rows: list[dict[str, Any]] = [
        {
            "attr": "tag",
            "value": str(entry.get("tag") or ""),
            "mode": None,
            "checked": True,
            "locked": True,
        }
    ]
    entry_id = entry.get("id")
    if entry_id:
        rows.append(
            {
                "attr": "id",
                "value": str(entry_id),
                "mode": ATTR_EQUALS,
                "checked": f"#{entry_id}" in fragment,
                "locked": False,
            }
        )
    for cls in entry.get("classes") or []:
        rows.append(
            {
                "attr": "class",
                "value": str(cls),
                "mode": ATTR_EQUALS,
                "checked": f".{cls}" in fragment,
                "locked": False,
            }
        )
    nth = entry.get("nthOfType")
    if isinstance(nth, int) and not isinstance(nth, bool) and nth >= 1:
        rows.append(
            {
                "attr": "nth-of-type",
                "value": str(nth),
                "mode": None,
                "checked": f":nth-of-type({nth})" in fragment,
                "locked": False,
            }
        )
    return rows


def node_label(entry: dict[str, Any]) -> str:
    """一级节点的树行文本：**节点类型 + 该级勾选的属性**（对齐影刀那一屏）。

    影刀的行是 ``div#kw.s-hotsearch-content`` 这样——一眼看到「这是个 div、
    靠 id 和 class 定位」；我们此前摆的是**编译后的 fragment**（同样信息，但
    还混着``:nth-of-type(2)``、``.cls1.cls2`` 这些「第几层、哪些类」的拼接细节，
    且看不出「哪个属性是勾上的」——属性表在右半，得来回对照）。

    这里按**已勾选**的属性重建（口径与 :func:`attribute_rows` / :func:`compile_fragment`
    严格一致：``id`` 勾选→ ``#id``；class 逐个勾选 → ``.cls``；nth 勾选 →
    ``:nth-of-type(n)``；tag恒在）。所以**这一行就是该级 fragment 的同源呈现**，
    不会出现「树里写的和真selector 不一致」的两套口径。

    刻意不显示未勾选的属性：那正是用户要去右半属性表里勾的东西，显示出来等于
    暗示它已经参与定位。
    """
    tag = str(entry.get("tag") or "").strip()
    fragment = str(entry.get("fragment") or "")
    rows = attribute_rows(entry, fragment)
    id_row = next((row for row in rows if row["attr"] == "id"), None)
    if id_row and id_row["checked"]:
        # 与 compile_fragment 同口径：id 等值命中即整层（tag 不再参与）。
        return "#" + str(id_row["value"]) if not tag else f"{tag}#{id_row['value']}"
    parts = [tag] if tag else []
    if id_row and id_row["checked"]:
        parts.append(f'[id*="{id_row["value"]}"]')
    parts.extend(
        "." + str(row["value"]) for row in rows if row["attr"] == "class" and row["checked"]
    )
    nth_row = next((row for row in rows if row["attr"] == "nth-of-type"), None)
    if nth_row and nth_row["checked"]:
        parts.append(f":nth-of-type({nth_row['value']})")
    return "".join(parts) or tag or "?"


def compile_fragment(entry: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """按属性表的勾选与匹配方式，把一层重新拼成 fragment。

    编译规则（与捕获口径兼容，css 消费方照旧是 ``querySelectorAll``）：
    - id 勾选且「等于」→ 整层就是 ``#id``（等值 id 本身唯一，tag/class/nth
      不再参与——沿用 content.js「带 id 即终止上溯」的口径）；
    - 其余情况 tag 恒在：id 勾选且「包含」→ ``[id*="…"]``；class 勾选 →
      「等于」``.cls`` / 「包含」``[class*="…"]``；nth 勾选 → ``:nth-of-type(n)``。
    """
    id_row = next((row for row in rows if row["attr"] == "id"), None)
    if id_row and id_row["checked"] and id_row.get("mode") == ATTR_EQUALS:
        return "#" + str(id_row["value"])
    out = str(entry.get("tag") or "")
    if id_row and id_row["checked"]:
        out += f'[id*="{_escape_css_attr(str(id_row["value"]))}"]'
    for row in rows:
        if row["attr"] != "class" or not row["checked"]:
            continue
        value = str(row["value"])
        if row.get("mode") == ATTR_CONTAINS:
            out += f'[class*="{_escape_css_attr(value)}"]'
        else:
            out += "." + value
    nth_row = next((row for row in rows if row["attr"] == "nth-of-type"), None)
    if nth_row and nth_row["checked"]:
        out += f":nth-of-type({nth_row['value']})"
    return out


class PreviewShot(QWidget):
    """「预览」页签：窗口截图 + 首个命中元素的**红框**（对齐影刀那一屏）。

    截图来源（M47.12）：**host 统一走桌面坐标截屏**——``PIL.ImageGrab.grab(bbox=
    窗口绝对矩形)``，与影刀同一路。维护者实测影刀「会把浏览器前面窗口的内容也截取
    到」，那正是坐标截屏的特征（截的是屏幕上那块矩形），所以这不是缺陷、是我们的口径。

    为什么统一到桌面而不用 ``captureVisibleTab``：桌面腿（WinForms/Qt 控件）压根**没有**
    「页面」可截，而两条通道各拍各的会让控件与控件长得一模一样、换算公式也两套
    （视口截图要 dpr、坐标截屏要窗口原点）。统一后浏览器与桌面共用同一个控件、
    同一种 ``box`` 形状，换算在 ``capture.screen_shot`` 里是两条纯函数。

    **红框坐标换算在 :mod:`rpa_core.capture.screen_shot` 里做**（纯函数、可测）：
    桌面腿的元素矩形本就是屏幕坐标、与图同源 1:1；浏览器腿要把视口 CSS 像素
    换算过去（差一个 ``图宽/视口宽`` 的缩放与一个视口原点偏移）。这里只负责把算好的框
    画到**控件坐标系**上——控件会把图缩放到自身宽度，所以画之前还要按
    ``控件宽 / 图宽`` 再缩一次。

    各种状态都有明确文案，绝不停在「一片空白」让用户猜：
    没截图 → 「尚未获取预览截图」；有图但没命中 → 「本页未命中该选择器」；
    有图有命中但框算不出来 → 「已命中，但元素不在窗口内（无框可画）」。

    刻意**不缩放图**（只画框跟随缩放）：用户要看的是「元素在页面哪儿」，缩小到全图
    塞进控件会让截图细节全看不清；图按原始像素居中显示，超出部分裁掉。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._box: dict[str, float] | None = None
        self._message = "尚未获取预览截图"
        self.setMinimumSize(360, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    #-- 数据 ----------------------------------------------------------------

    def clear(self, message: str = "尚未获取预览截图") -> None:
        self._pixmap = None
        self._box = None
        self._message = message
        self.update()

    def show_shot(self, data_url: str, box: dict[str, float] | None) -> bool:
        """显示一张截图与（可选的）红框。返回图是否**解出来了**。

        ``data_url`` 是 ``data:image/png;base64,...``（host 坐标截屏的产物）。
        解不出来（不是 data URL / base64 坏/ 空图）时**不抛**：调用方据此把文案改成
        「截图无法显示」，而校验命中数仍然有效——截图是观感增强。
        """
        pixmap = decode_shot(data_url)
        if pixmap is None or pixmap.isNull():
            self._pixmap = None
            self._box = None
            self._message = "截图已回传但无法显示（base64 解码失败）"
            self.update()
            return False
        self._pixmap = pixmap
        self._box = box
        self._message = "" if box else "已命中，但元素不在窗口内（无框可画）"
        self.update()
        return True

    @property
    def has_shot(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    @property
    def box(self) -> dict[str, float] | None:
        """图内像素坐标下的红框（判据与探针要看它，故暴露只读）。"""
        return dict(self._box) if self._box else None

    @property
    def message(self) -> str:
        return self._message

    # -- 绘制 ----------------------------------------------------------------

    def _image_rect(self) -> tuple[int, int, int, int]:
        """图在控件里的落位（居中、不缩放；返回 (x, y, w, h)）。"""
        assert self._pixmap is not None
        iw, ih = self._pixmap.width(), self._pixmap.height()
        cw, ch = self.width(), self.height()
        scale = min(1.0, cw / iw, ch / ih) if iw and ih else 1.0
        w, h = int(iw * scale), int(ih * scale)
        return (cw - w) // 2, (ch - h) // 2, w, h

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(self.backgroundRole()))
        if not self.has_shot:
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        x, y, w, h = self._image_rect()
        painter.drawPixmap(x, y, w, h, self._pixmap)
        if self._box:
            # 控件缩放比例：图在控件里被缩到了 w 宽，红框必须按同一比例缩，否则框会
            # 贴在图外（这正是把「图内像素」换算成「控件像素」这一步的用处）。
            iw = self._pixmap.width() or 1
            k = w / iw
            bx = x + float(self._box["x"]) * k
            by = y + float(self._box["y"]) * k
            bw = max(2.0, float(self._box["width"]) * k)
            bh = max(2.0, float(self._box["height"]) * k)
            painter.setPen(QPen(QColor(DANGER), 2))
            painter.drawRect(QRectF(bx, by, bw, bh))
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(
            self.rect().adjusted(4, 4, -4, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            self._message,
        )


def decode_shot(data_url: Any) -> QPixmap | None:
    """``data:image/png;base64,...`` → ``QPixmap``；任何不合法输入返回 ``None``。

    刻意**不抛**：这张图是校验通道的**附加产物**，一张坏图不该把「命中 N 个」这条
    真正的判据一起带崩（neg-verification H4钉的就是这条）。
    """
    if not isinstance(data_url, str) or "," not in data_url:
        return None
    head, _, payload = data_url.partition(",")
    if not head.startswith("data:image/") or "base64" not in head:
        return None
    try:
        blob = base64.b64decode(payload.strip(), validate=True)
    except (ValueError, binascii.Error):
        return None
    if not blob:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(blob, "PNG"):
        return None
    return pixmap


class _PreviewDone(QObject):
    """预览结果跨线程回传：工作线程 emit，对话框线程收（Qt 队列连接保证）。"""

    # object 载荷：{"kind": "preview"|"clear", "seq": N, ...通道结果}
    done = Signal(object)


class ElementEditorForm(QWidget):
    """元素定位的编辑区：浏览器（主 css + 候选提升）/ 桌面（locator 字段勾选）。

    **抽成独立控件，是为了让「捕获确认框」与「元素库编辑器」复用同一份编辑逻辑**——
    影刀的「捕获即编辑」意味着这两者本是同一屏；我们此前是两步（确认框 → 另开编辑器）。
    合并时若各写一套，结果必然是「同一件事两处口径」，而它迟早漂移成
    「确认框里改得动的东西，编辑器里报错」。只放**编辑与校验**；「展示什么」
    （只读 metadata 行集）留在 ``element_panel``。

    校验的权威始终是**模型**（``locator_problems`` / ``css_problems``）：界面不另立
    一套规则，所以这里报的错与执行器、``selector_errors`` 完全同源。
    """

    #: 任一编辑动作后触发（值已变化）。外壳用它决定「确定」是否可用。
    changed = Signal()

    def __init__(self, document: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._kind = document.get("kind")
        self._verify_count = int(document.get("verifyCount") or 0)
        raw_selector = _dict_or_empty(document.get("selector"))
        self._extras = {
            key: value
            for key, value in raw_selector.items()
            if key not in {"css", "locator", "candidates"}
        }
        self._candidates: list[dict[str, Any]] = [
            dict(item)
            for item in raw_selector.get("candidates") or []
            if isinstance(item, dict)
        ]
        # 节点树原料：捕获回传的祖先链（每级 {tag, id, classes, nthOfType, fragment}）。
        # 只认「有非空 fragment」的级——老元素没有 path、手工文档可能形状不齐，缺了
        # 就不建树（树是加分项，不是门槛）。
        self._path: list[dict[str, Any]] = [
            dict(entry)
            for entry in raw_selector.get("path") or []
            if isinstance(entry, dict)
            and isinstance(entry.get("fragment"), str)
            and entry["fragment"]
        ]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 就地校验的展示面。**必须在建行之前创建**：建表过程末尾就会触发一次校验
        # （桌面表按 backend 决定显示哪些行），那时 label 还不存在就是一个 AttributeError。
        # 创建早、加进布局晚——位置仍在校验区。
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(f"color: {DANGER};")

        # 捕获时快照的字段必须在**建腿之前**就位：_run_shot 会读它，而桌面腿
        # 同样会走到那里（第一版放在 _build_browser 里 ⇒ 桌面腿 AttributeError，
        # 由 test_desktop_preview_screenshot_takes_no_css_argument 抓出来）。
        # 装载则放在 _build_browser 里（那里才有 css_edit 可读）。
        self._capture_shot: dict | None = None
        self._capture_shot_css: str = ""

        if self._kind == "browser":
            self._build_browser(layout)
        else:
            self._build_desktop(layout, raw_selector)

        layout.addWidget(self.info_label)
        self.revalidate()

    # -- 浏览器 --------------------------------------------------------------

    def _build_browser(self, layout: QVBoxLayout) -> None:
        """browser 分支：**影刀式两页签**（预览 / 精准定位）+ 底部选择器单选。

        结构照影刀的「元素编辑器」那一屏摆（M47.11，维护者给了对照截图）：

        ======================  ==========================================
        影刀                      我们
        ======================  ==========================================
        元素名称输入框捕获确认框（``ElementDialog.name_edit``）
        绿色「已找到 1 个元素」    ``preview_label``（命中数唯一落点）
        页签：预览 / 精准定位      ``QTabWidget`` 同名两页
        「预览」= 截图 + 红框      ``PreviewShot``（扩展侧 captureVisibleTab 回传）
        「精准定位」= DOM 树 + 属性表节点树 + 属性表（并排，见 ``_build_path_tree``）
        底部：默认选择器 / XPath   ``_build_selector_choice``（XPath **置灰**，见下）
        底部：锚点 + 添加          「添加」**置灰**（见 ``_build_anchor_row``）
        ======================  ==========================================

        **AI 辅助定位页签不摆**（维护者明确「可以先不做」）：摆一个点不开的空页签
        比不摆更糟——用户会以为功能坏了。

        **备选定位 UI 整体移除**（维护者「备选移除吧」，且实测「几次捕捉都没看到有备选
        定位」）。注意：移除的只是**界面**，``selector.candidates`` 在
        ``result_document`` 里**原样带回**——运行期自愈（``executors.browser``
        的 ``_element_candidates``）仍按它反查回退，悄悄丢掉数据等于悄悄拆掉M28。
        """
        selector = _dict_or_empty(self._document.get("selector"))

        # 命中数标签：browser 分支的「已找到 N 个」。摆**在页签之上**（影刀的位置），
        # 两个页签共用它——切页签不该让「命中几个」这件事消失。
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.preview_label.hide()
        layout.addWidget(self.preview_label)

        # 主选择器输入：放在页签**之上**（常驻），而不是塞进「精准定位」页里——
        # 它是主入口，两个页签都要看得见它；藏进某一页签等于「看截图时看不到选择器」。
        form = QFormLayout()
        # 用**多行**输入（SelectorEdit）：长祖先链一行装不下，QLineEdit 会把尾巴截掉
        # （维护者实测「主选择器的框太小了，不方便」）。对外仍是 .text()/.setText()。
        self.css_edit = SelectorEdit(str(selector.get("css") or ""))
        self.css_edit.textChanged.connect(self.revalidate)
        form.addRow("主选择器（css）", self.css_edit)
        layout.addLayout(form)

        self.tabs = QTabWidget()
        # 「预览」页：截图 + 红框。
        self.preview_shot = PreviewShot()
        self.tabs.addTab(self.preview_shot, "预览")
        # 「精准定位」页：节点树 + 属性表（并排）。
        self.locate_page = QWidget()
        self._build_path_tree(self.locate_page)
        self.tabs.addTab(self.locate_page, "精准定位")
        # **默认停在「精准定位」**（M47.12，维护者「默认展示精准定位便签页」）。
        # 用户进编辑器的第一诉求是「改选择器」，那棵树和属性表才是主力；截图是
        # 改完之后的确认手段。附带好处：默认页不是预览 ⇒ 开框不会顺手拍一张
        # 几百KB 的PNG。
        self.tabs.setCurrentIndex(1)
        # 页签切换：**只在切到「预览」时才跑一次截图**。影刀是进页签才截，我们
        # 照做——每次改 css 都截一张既慢又占内存，而用户在「精准定位」页里
        # 改选择器时根本不看图。
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self._build_selector_choice(layout)
        self._build_anchor_row(layout)

        # 编辑中预览（M48/C3）：改动 css 后 500ms 防抖，页面上驻留高亮当前命中。
        # 通道回调由调用方注入（`enable_live_preview`，app 侧只对 browser 元素接）；
        # 未注入时标签保持隐藏、计时器永不启动——桌面元素没有「页面」可高亮。
        self._preview_css: Callable[[str], dict] | None = None
        self._clear_preview_css: Callable[[], dict] | None = None
        self._shot_css: Callable[[str], dict] | None = None
        self._preview_seq = 0
        self._shot_seq = 0
        self._preview_signal = _PreviewDone()
        self._preview_signal.done.connect(self._on_preview_done)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._run_preview)
        self.css_edit.textChanged.connect(self._on_css_changed)
        # 捕获时快照直接装载：**唯一来源是文档本身**，不靠调用方记得调
        # set_capture_shot（上一版挂在 ElementDialog 上，元素库编辑器那条路就漏了——
        # 正是「两处各自绿 ≠ 链路通」的同类坑）。只能放在 browser 腿里：
        # set_capture_shot 要读 css_edit，桌面腿没有这个控件。
        self.set_capture_shot(self._document.get("captureShot"))

    def _build_selector_choice(self, layout: QVBoxLayout) -> None:
        """底部「默认选择器 / XPath」单选（影刀那一屏的最底行）。

        XPath **摆出来但置灰**：用户明确点名要过这个（相比影刀少了它），所以不能装作
        没有；但全链路只认 CSS（``page_call`` 没有 ``document.evaluate``，``selector.kind``
        也不是契约判别子），真放一个可选的 XPath 就是**假功能**——用户选它、保存、
        运行期静默按 css 走。置灰 + 悬浮说明「正在做」比能点但没用诚实。
        （实现见 M51；一旦落地，只要把 ``setEnabled(True)`` 去掉即可。）
        """
        row = QHBoxLayout()
        row.addWidget(QLabel("选择器"))
        self.selector_default_radio = QRadioButton("默认选择器")
        self.selector_default_radio.setChecked(True)   # 唯一可选的那项
        self.selector_xpath_radio = QRadioButton("XPath")
        self.selector_xpath_radio.setEnabled(False)
        self.selector_xpath_radio.setToolTip(
            "XPath 选择器尚未接入执行链路（正在实现）。"
            "现在启用会让「选了就以为生效」，运行期仍按 css 执行。"
        )
        row.addWidget(self.selector_default_radio)
        row.addWidget(self.selector_xpath_radio)
        row.addStretch(1)
        layout.addLayout(row)

    def _build_anchor_row(self, layout: QVBoxLayout) -> None:
        """底部「锚点 + 添加」行：整个功能**置灰**（维护者「锚点单独立功能项」）。

        浏览器元素的锚点（``selector.anchor``）在模型里还没有消费方——摆一个能点的
        「添加」只会写下一个运行期没人读的字段，正是本项目最忌讳的静默死字段。
        桌面腿的锚点编辑是**另一个**入口（``_build_anchor_view``），那边是真能用的。
        """
        row = QHBoxLayout()
        row.addWidget(QLabel("锚点"))
        self.anchor_hint = QLabel("（锚点定位正在实现，暂不可用）")
        self.anchor_hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.anchor_add_button = QPushButton("添加")
        self.anchor_add_button.setEnabled(False)
        self.anchor_add_button.setToolTip(
            "锚点定位正在实现。浏览器元素的 anchor 目前没有运行期消费方，"
            "这里放一个能点的「添加」只会写下一个没人读的字段。"
        )
        row.addWidget(self.anchor_hint)
        row.addStretch(1)
        row.addWidget(self.anchor_add_button)
        layout.addLayout(row)

    # -- 浏览器：节点树（勾层级 → 拼回主选择器） -----------------------------

    def _build_path_tree(self, container: QWidget) -> None:
        """节点树：按捕获回传的祖先链（``selector.path``）逐级勾选。

        **单向**（树 → 主选择器）。``css_edit`` 是唯一落盘口径；树是「从捕获路径
        重新拼选择器」的入口。不做双向同步是有意的：把 css 解析回层级不可靠
        （手改的 css 不出自这条路径），硬做双向就是立第二套口径、迟早互相改写。
        所以树只承诺一件事——**再次勾选，就按所选层级重写主选择器**；两个入口写同一个
        字段，后动手的赢。

        ``container`` 是承载它的页（``QTabWidget`` 里那一页），不是布局：影刀把
        「精准定位」做成独立页签，节点树与属性表都在**那一页里面**并排。
        """
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        if not self._path:
            # 老元素没有 path：不建树也不摆一个空壳。但**要说清为什么是空的**——
            # 空白的「精准定位」页会被当成「加载失败」或「这元素不支持」。
            empty = QLabel(
                "该元素没有捕获时的节点路径（老元素或手工文档），"
                "无法按层级勾选；可直接在上方编辑主选择器。"
            )
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {TEXT_SECONDARY};")
            layout.addWidget(empty)
            layout.addStretch(1)
            self.path_list = None
            self.attr_table = None
            return
        self.path_label = QLabel(
            "节点路径（勾选参与定位的层级；勾选会重写主选择器）"
        )
        self.path_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.path_label)

        # 节点树与属性表**并排**（M47.10，维护者要求）：树在左、属性表在右，中间可拖。
        # 此前是上下堆叠——树的每一行是一条祖先链层级，属性表是「选中那层的属性」，
        # 两者是「选级 → 看/改该级」的联动关系，并排后联动一眼可见，也不必上下滚动。
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.path_list = QListWidget()
        self.path_list.setMinimumHeight(150)
        self._composing_path = False
        for index, entry in enumerate(self._path):
            item = QListWidgetItem(node_label(entry))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            # tooltip 补全 fragment 与序号：行内为了紧凑只摆「类型 + 勾选属性」。
            extra = entry.get("id") or (entry.get("classes") or "")
            item.setToolTip(
                f"层级 {index + 1}/{len(self._path)} · tag={entry.get('tag') or '?'}"
                + (f" · {extra}" if extra else "")
                + f"\n参与定位的片段：{entry.get('fragment')}"
            )
            self.path_list.addItem(item)
        self.path_list.itemChanged.connect(self._on_path_item_changed)
        splitter.addWidget(self.path_list)
        self._build_attr_table(splitter)   # 表只进右半
        # **两栏均分**（M47.12，维护者「节点路径和属性框均分即可」）。此前是 1:2
        # （`setSizes([180, 360])`），理由是「属性表 4 列需要更宽」——实测树那几行
        # 加上类型与勾选属性之后并不窄，而1:2 让树右侧留白、显得空。
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 260])
        self.path_splitter = splitter
        layout.addWidget(splitter, 1)
        self._sync_path_checks_from_css()
        # 默认选中目标所在层（末级）：属性表打开即有内容可看
        self.path_list.setCurrentRow(len(self._path) - 1)

    # -- 浏览器：属性表（A1，勾属性/改匹配方式 → 重写该层 fragment） ----------

    def _build_attr_table(self, container) -> None:
        """属性表：对**树中选中的一层**逐属性勾选、改匹配方式。

        节点树回答「参与定位的是哪几级」，属性表回答「这一级里哪些属性参与、
        精确还是模糊」——影刀编辑器右侧三列表（属性名 | 匹配方式 | 属性值）
        的离线版。「包含」编译成 CSS 属性选择器（``[class*="…"]``），
        消费方照旧是执行器的 ``querySelectorAll``，不需要动执行器。
        落盘口径仍唯一：所有编辑最终都只是重写 ``css_edit``（经
        ``_on_path_item_changed`` 的同一条组装路径）。

        ``container`` 是并排区的右半（QSplitter）；标题 ``attr_label`` 由调用方
        摆在并排区之上，这里只建表。
        """
        self._composing_table = False
        self._attr_rows: list[dict[str, Any]] = []

        self.attr_table = QTableWidget(0, 4)
        self.attr_table.setHorizontalHeaderLabels(["参与", "属性", "匹配方式", "值"])
        self.attr_table.verticalHeader().setVisible(False)
        self.attr_table.verticalHeader().setDefaultSectionSize(30)
        self.attr_table.setAlternatingRowColors(True)
        self.attr_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.attr_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.attr_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.attr_table.horizontalHeader().setStretchLastSection(True)
        # 维护者实测：160px 上限只装得下 3~4 行，「不方便查看选择」。改成
        # 最小高度 + Expanding——表格随对话框变大而变大，窄内容列不抢宽度。
        self.attr_table.setMinimumHeight(220)
        self.attr_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        container.addWidget(self.attr_table)
        self.path_list.currentRowChanged.connect(
            lambda _row: self._rebuild_attr_table()
        )
        self._rebuild_attr_table()

    def _rebuild_attr_table(self) -> None:
        """按树中当前选中的一层重建属性表（勾选态从该层 fragment 反推）。"""
        row = self.path_list.currentRow()
        self._composing_table = True
        try:
            self.attr_table.setRowCount(0)
            if not (0 <= row < len(self._path)):
                return
            entry = self._path[row]
            self._attr_rows = attribute_rows(entry, str(entry["fragment"]))
            self.attr_table.setRowCount(len(self._attr_rows))
            for index, spec in enumerate(self._attr_rows):
                box = QCheckBox()
                box.setChecked(spec["checked"])
                box.setEnabled(not spec["locked"])
                box.toggled.connect(self._on_attr_edited)
                self.attr_table.setCellWidget(index, 0, box)
                name_item = QTableWidgetItem(spec["attr"])
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.attr_table.setItem(index, 1, name_item)
                if spec["mode"] is not None:
                    combo = QComboBox()
                    combo.addItems(["等于", "包含"])
                    combo.setCurrentIndex(
                        0 if spec["mode"] == ATTR_EQUALS else 1
                    )
                    combo.currentIndexChanged.connect(self._on_attr_edited)
                    self.attr_table.setCellWidget(index, 2, combo)
                else:
                    dash = QTableWidgetItem("—")
                    dash.setFlags(dash.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    dash.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.attr_table.setItem(index, 2, dash)
                value_item = QTableWidgetItem(spec["value"])
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.attr_table.setItem(index, 3, value_item)
        finally:
            self._composing_table = False

    def _on_attr_edited(self, *_args: Any) -> None:
        """属性表任何改动：重编译该层 fragment → 更新树行文本 →
        经 ``itemChanged`` 走既有的「勾选层级拼回 css」路径重写主选择器。"""
        if getattr(self, "_composing_table", False):
            return
        row = self.path_list.currentRow()
        if not (0 <= row < len(self._path)):
            return
        rows: list[dict[str, Any]] = []
        for index, spec in enumerate(self._attr_rows):
            current = dict(spec)
            box = self.attr_table.cellWidget(index, 0)
            combo = self.attr_table.cellWidget(index, 2)
            if box is not None:
                current["checked"] = box.isChecked()
            if combo is not None:
                current["mode"] = (
                    ATTR_EQUALS if combo.currentIndex() == 0 else ATTR_CONTAINS
                )
            rows.append(current)
        self._attr_rows = rows
        fragment = compile_fragment(self._path[row], rows)
        self._path[row]["fragment"] = fragment
        # 树行文本即 fragment：更新它会触发 itemChanged → _on_path_item_changed
        # （同一条组装路径，css 与树不可能漂移）
        self.path_list.item(row).setText(fragment)

    def _sync_path_checks_from_css(self) -> None:
        """按当前主 css 反推勾选态（只在建树时做一次）。

        用户对路径的典型操作是**截短祖先**（css 是路径的后缀），所以从最短后缀
        找起：css = 全路径 → 全勾；css = 末两级 → 只勾末两级。匹配不到（css 不出自
        这条路径，比如候选提升来的）就保持全勾——树退回「完整路径」视角，
        下一次勾选才重写。
        """
        if not hasattr(self, "path_list"):
            return
        fragments = [str(entry["fragment"]) for entry in self._path]
        css = self.css_edit.text().strip()
        start = next(
            (
                k
                for k in range(len(fragments))
                if " > ".join(fragments[k:]) == css
            ),
            0,
        )
        self._composing_path = True
        try:
            for index in range(self.path_list.count()):
                state = (
                    Qt.CheckState.Checked if index >= start else Qt.CheckState.Unchecked
                )
                self.path_list.item(index).setCheckState(state)
        finally:
            self._composing_path = False

    def _on_path_item_changed(self, _item: QListWidgetItem) -> None:
        if getattr(self, "_composing_path", False):
            return
        last_row = self.path_list.count() - 1
        # 末级是目标本身：不勾它，选择器就不再指向捕获的那个元素。
        # 这里替用户勾回去不是越权——「不指向目标的定位方案」不成立，没得选。
        if self.path_list.item(last_row).checkState() != Qt.CheckState.Checked:
            self._composing_path = True
            try:
                self.path_list.item(last_row).setCheckState(Qt.CheckState.Checked)
            finally:
                self._composing_path = False
        fragments = [
            str(entry["fragment"])
            for index, entry in enumerate(self._path)
            if self.path_list.item(index).checkState() == Qt.CheckState.Checked
            or index == last_row
        ]
        css = " > ".join(fragments)
        if css != self.css_edit.text().strip():
            self.css_edit.setText(css)

    # -- 浏览器：编辑中预览（M48/C3，通道回调注入式） -------------------------

    def enable_shot_channel(self, shot: Callable[..., dict]) -> None:
        """桌面腿接上截图通道（``shot()`` 无参：窗口与元素矩形已在服务层封好）。

        与 :meth:`enable_live_preview` 刻意**分开**：桌面腿没有「页面」可高亮，
        没有 css 改动可防抖，所以「命中数实时标签 + 驻留预览」整条都不适用，
        只剩「点开预览页拍一张」。混在一个方法里会留下一堆
        ``if kind == "browser"`` 分支。

        **页签已经在 :meth:`_build_desktop` 里摆出来了**（默认停在「精准定位」），
        没接通道时切过去只会看到「尚未获取预览截图」——与 browser 腿未注入时同一
        口径：不藏页签，藏掉比「这一页暂时没内容」更难解释。
        """
        self._shot_css = shot

    def enable_live_preview(
        self,
        preview: Callable[[str], dict],
        clear: Callable[[], dict],
        shot: Callable[..., dict] | None = None,
    ) -> None:
        """接上预览通道：css 每次改动后防抖驻留高亮，收场清场由 ``shutdown_preview``。

        ``shot(css, want_shot=True)`` 是**可选**的截图通道（M47.11）：接上之后切到
        「预览」页签才会拍一张带红框的页面截图。没接时该页签仍摆出来（影刀那一屏的
        结构），只是永远停在「尚未获取预览截图」——**不隐藏页签**：用户要的是那两页
        的布局，藏掉一页比「这一页暂时没内容」更难解释。

        桌面元素没有「页面」可高亮，不注入回调即整体不生效（标签都不出现）。
        """
        if not hasattr(self, "css_edit"):
            return  # 桌面表单没有主 css 行，预览无从谈起
        self._preview_css = preview
        self._clear_preview_css = clear
        self._shot_css = shot
        self.preview_label.show()
        # 打开编辑器就先预跑一轮：用户还没动键盘也能立刻看到「这条 css 现在命中几个」
        self._preview_timer.start()

    def shutdown_preview(self) -> None:
        """收场清场（对话框 finished 时调用）：停表 + 发 ``mode="clear"`` 收走黄框。

        预览框不能陪用户关掉编辑器后赖在页面上。清场是 fire-and-forget（工作线程），
        迟到结果由 ``_preview_seq`` 作废。
        """
        self._preview_timer.stop()
        if self._clear_preview_css is None:
            return
        self._preview_seq += 1
        self._dispatch(self._clear_preview_css, ())

    def _on_tab_changed(self, index: int) -> None:
        """切到「预览」页签 → 跑一轮**带截图**的校验。

        只在切到预览页时拍（M47.11）：每敲一个 css 字符就截一张base64 PNG 既慢又占
        内存，而用户在「精准定位」页里改选择器时根本不看图。开框时已经在预览页（索引
        0）——那次由 :meth:`enable_live_preview` 的预跑负责，不重复拍。
        """
        if index == 0:
            self._run_shot()

    def set_capture_shot(self, shot: dict | None) -> None:
        """接收**捕获那一刻**的窗口快照（``descriptor["captureShot"]``），直接展示。

        为什么优先用它而不是「点预览时重新截」（M47.12 真机撞出来的）：
        「点预览时截」依赖 ``chrome.windows.get().nativeWindowHandle``，真实环境里
        **未必给得出**（2026-10-09：扩展已是0.8.0，Edge 上仍 ``has_hwnd=false``），
        于是预览页签永远停在「未能定位到浏览器窗口」——那句话还把用户引向错误的
        处置（去切窗口，切一百次也没用）。捕获时则由 host 用 Z 序认窗口
        （:func:`first_browser_window`，Chrome/Edge 都在册），**不依赖扩展**。

        快照与「当前 css」绑定：用户改了选择器之后那张图就过期了，此时
        :meth:`_run_shot` 会自动退回实时截屏，而不是拿旧图冒充新选择器。

        **不会**被写进元素库：:meth:`result_document` 只挑 ``kind``/``selector``/
        ``verifyCount``/``metadata`` 四个键，base64 PNG 不在其中（几十万字符进
        元素库既臃肿又毫无用处）。
        """
        if not isinstance(shot, dict):
            return
        data_url = shot.get("dataUrl")
        if not isinstance(data_url, str) or not data_url:
            return
        # 桌面腿没有 css_edit（也没有 preview_shot 的浏览器语义）：它的截图由
        # _desktop_preview_callable 在服务层封好窗口与矩形。这里直接返回，不去碰
        # 不存在的控件——否则桌面元素一旦被塞进 captureShot 就是 AttributeError。
        if not hasattr(self, "css_edit") or not hasattr(self, "preview_shot"):
            return
        self._capture_shot = shot
        # 记下快照对应的 css：只有选择器没动过，它才算「这张就是你要看的那张」。
        self._capture_shot_css = self.css_edit.text().strip()
        self.preview_shot.show_shot(data_url, shot.get("box"))

    def _run_shot(self) -> None:
        """取一张截图 + 首个命中的红框，填进「预览」页。

        两条腿同一个控件、同一条通道回调，但**入参形状不同**：

        - browser：入参是 css 字符串（要先校验才知道命中谁）；
        - desktop：没有 css 概念，元素**就是捕获时那一个**⇒ 入参是空的，
          窗口句柄与元素矩形由 :meth:`enable_shot_channel` 注入时就已经封好了。

        没有截图通道（未注入 / 非win32）时只更新文案，不发请求。
        """
        if not hasattr(self, "preview_shot"):
            return
        # **捕获时快照优先**（M47.12 真机反馈）：它就是用户要看的那张图，且不依赖
        # 扩展给窗口句柄。失效条件只有一个——选择器被改过（那张图对不上新 css）。
        if self._capture_shot is not None and self._kind != "desktop":
            if self._capture_shot_css == self.css_edit.text().strip():
                self.preview_shot.show_shot(
                    str(self._capture_shot.get("dataUrl") or ""),
                    self._capture_shot.get("box"),
                )
                return
            # 选择器改了 ⇒ 快照过期：丢掉它，让实时截屏接管（别拿旧图冒充）。
            self._capture_shot = None
        if self._shot_css is None:
            self.preview_shot.clear(
                "尚未获取预览截图（当前环境未接入截图通道）"
            )
            return
        if self._kind == "desktop":
            # 桌面腿：不需要选择器，直接拍（入参留空——服务层已绑定窗口与矩形）。
            self._shot_seq += 1
            self._dispatch(self._shot_css, (), kind="shot", seq=self._shot_seq)
            return
        css = self.css_edit.text().strip()
        if not css:
            self.preview_shot.clear("先填写主选择器再取预览截图")
            return
        self._shot_seq += 1
        self._dispatch(self._shot_css, (css,), kind="shot", seq=self._shot_seq)

    def _on_css_changed(self) -> None:
        if self._preview_css is None:
            return
        # 防抖重启：连续键入只打最后一轮，不把每敲一个字符都变成一次页面往返
        self._preview_timer.start()

    def _run_preview(self) -> None:
        css = self.css_edit.text().strip()
        if not css:
            # 空选择器没有可预览的对象：就地清场（旧框不能赖着冒充命中）
            self.preview_label.setText("")
            if hasattr(self, "preview_shot"):
                self.preview_shot.clear("先填写主选择器再取预览截图")
            if self._clear_preview_css is not None:
                self._preview_seq += 1
                self._dispatch(self._clear_preview_css, ())
            return
        self.set_hit_label("查找中…", TEXT_SECONDARY)
        self._preview_seq += 1
        self._dispatch(self._preview_css, (css,))

    def _dispatch(
        self,
        callback: Callable[..., dict],
        args: tuple,
        *,
        kind: str = "",
        seq: int | None = None,
    ) -> None:
        # 「截图」与「驻留预览」用**各自独立**的 seq：陈旧的截图回传不该作废当前预览
        # 的命中数，反之亦然（两条通道节奏不同：一个是 500ms 防抖，一个是切页签一次）。
        seq = self._preview_seq if seq is None else seq

        def work() -> None:
            try:
                result = dict(callback(*args))
            except Exception as exc:  # noqa: BLE001 - 通道故障也要落到标签上
                result = {"error": f"预览通道异常：{exc}"}
            if not kind:
                kind_ = "clear" if callback is self._clear_preview_css else "preview"
            else:
                kind_ = kind
            payload = {"kind": kind_, "seq": seq, **result}
            try:
                self._preview_signal.done.emit(payload)
            except RuntimeError:
                pass  # 表单已随对话框销毁：无处展示，daemon 线程自灭

        threading.Thread(target=work, daemon=True).start()

    def _on_preview_done(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        if data.get("kind") == "clear":
            return  # 清场无观感，静默即可
        if data.get("kind") == "shot":
            self._on_shot_done(data)
            return
        if data.get("seq") != self._preview_seq:
            return  # 陈旧结果：用户已改了下一轮，覆盖反而回退显示
        if data.get("error"):
            self.set_hit_label(f"预览失败：{data['error']}", DANGER)
            return
        count = data.get("count")
        if count == 1:
            text, color = "命中 1 个（页面上已黄框高亮）", SUCCESS
        elif isinstance(count, int) and not isinstance(count, bool) and count > 1:
            text, color = f"命中 {count} 个（超过 1 个不唯一）", DANGER
        else:
            text, color = "命中 0 个（页面上找不到该选择器）", DANGER
        self.set_hit_label(text, color)

    def _on_shot_done(self, data: dict) -> None:
        """截图回传落地（M47.12：截图已由 host 的坐标截屏算好 ``box``）。

        与 M47.11 的一处关键差别：**图内红框不再在这里算**。坐标截屏的图左上角 =
        窗口左上角，而浏览器元素矩形是视口 CSS 像素，两者换算需要「窗口绝对矩形」
        （只有 host 拿得到 win32 ``GetWindowRect``）＋ 视口屏幕原点（只有扩展能给）。
        所以整段换算被收在 :func:`rpa_core.capture.screen_shot.browser_preview_shot`
        里做完，这里只负责展示与降级文案。

        顺序仍不能反：先解码图拿到尺寸（控件按图宽缩放，红框得跟同一比例），
        最后一次性交给 :class:`PreviewShot`。
        """
        if data.get("seq") != self._shot_seq:
            return  # 陈旧截图：用户已切走或又改了 css
        if data.get("error"):
            # 截图失败**不**改命中标签：那是「校验失败」，而截图只是观感增强。
            self.preview_shot.clear(f"预览截图失败：{data['error']}")
            return
        data_url = data.get("dataUrl")
        if not isinstance(data_url, str) or not data_url:
            # 要了图但没拿到：说清是哪一种，别停在空白页签让用户猜是不是功能坏了。
            # 五种原因（2026-10-09 真机反馈后补ext-stale）：见verify.shot_failure_reason。
            count = data.get("count")
            reason = data.get("shotError")
            if count == 0:
                self.preview_shot.clear("未命中该选择器，没有可预览的元素")
            elif reason == "ext-stale":
                # **最容易被误判成「代码坏了」的一种**：红框、校验都正常，只有新功能不生效。
                # 根因是 Load unpacked 载入即快照，浏览器里跑的还是旧版本。
                self.preview_shot.clear(
                    f"浏览器里跑的是旧版扩展（{data.get('extBuild') or '版本未知'}），"
                    "请到 chrome://extensions 点该扩展的刷新按钮后重试"
                )
            elif reason == "no-window-handle" or not data.get("windowHandle"):
                self.preview_shot.clear(
                    f"已命中 {count} 个，但未能定位到浏览器窗口（请先切到该浏览器窗口再预览）"
                )
            else:
                self.preview_shot.clear(
                    f"已命中 {count} 个，但截图失败（窗口最小化或锁屏会话时会这样）"
                )
            return
        self.preview_shot.show_shot(data_url, data.get("box"))

    def set_hit_label(self, text: str, color: str) -> None:
        """写**唯一**那条命中数标签（预览与「校验元素」共用同一条）。

        M47.10 去重：确认框曾有「当前命中 X 个」与编辑区的「预览：命中 X 个」两处，
        语义重叠且改 css 后会互相矛盾。现在两处来源都写这一条——「预览」前缀也去掉
        （来源已在场景里体现，不需要标签再声明一次它叫「预览」）。
        """
        if not hasattr(self, "preview_label"):
            return
        self.preview_label.setText(text)
        self.preview_label.setStyleSheet(f"color: {color};")

    # -- 桌面 ----------------------------------------------------------------

    def _build_desktop(self, layout: QVBoxLayout, raw_selector: dict) -> None:
        locator = _dict_or_empty(raw_selector.get("locator"))
        self._original_locator = locator
        # 结构字段（path / anchor）：不经过字符串管道，但要**原样带回**组装结果，
        # 否则用户在编辑器里点一次确定，捕获回传的祖先链/锚点就静默没了。
        self._structured: dict[str, Any] = structured_locator_values(locator)
        # 表格重绘期间挡住 itemChanged（否则 setItem 会触发 _on_path_table_changed 回环）。
        self._path_table_guard: bool = False
        # 两个后端的行都建出来，只显示当前 backend 的那一组：切换 backend 时值不丢，
        # 用户可以来回比较。组装时只看当前后端（见 _desktop_values）。
        metadata = _dict_or_empty(self._document.get("metadata"))
        suggested = {
            "controlType": metadata.get("controlType"),
            "automationId": metadata.get("automationId"),
            "name": metadata.get("name"),
            "title": metadata.get("title") or metadata.get("windowTitle"),
            "className": metadata.get("className"),
        }
        values = split_locator(locator)

        form = QFormLayout()
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(list(BACKENDS))
        backend = str(locator.get("backend") or "uia")
        if backend not in BACKENDS:
            self.backend_combo.addItem(backend)
        self.backend_combo.setCurrentText(backend)
        form.addRow("backend", self.backend_combo)

        self.field_edits: dict[str, QLineEdit] = {}
        self.field_boxes: dict[str, QCheckBox] = {}
        self.field_rows: dict[str, list[Any]] = {}
        for key in ALL_FIELD_KEYS:
            kind = field_kind(key)
            box = QCheckBox(key)
            box.setChecked(key in values)
            value = values.get(key)
            if value is None and suggested.get(key) is not None:
                # 捕获回传过但 locator 里没有的值：预填好、不勾 —— 等用户决定要不要用
                # （例：桌面捕获一直回传 className，而默认 locator 不含它）
                value = str(suggested[key])
            edit = QLineEdit(value or "")
            edit.setPlaceholderText("整数" if kind == "int" else field_hint(key))
            edit.textChanged.connect(
                lambda text, target=box: target.setChecked(bool(text.strip()))
            )
            box.toggled.connect(self.revalidate)
            edit.textChanged.connect(self.revalidate)
            self.field_boxes[key] = box
            self.field_edits[key] = edit
            self.field_rows[key] = [box, edit]
            row = QHBoxLayout()
            row.addWidget(box, 1)
            row.addWidget(edit, 2)
            form.addRow("", row)
        layout.addLayout(form)
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)

        self.field_hint = QLabel("")
        self.field_hint.setWordWrap(True)
        self.field_hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.field_hint)

        hint = QLabel(
            "勾选即写入 locator，取消即移除；值为空等于不勾。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(hint)
        # 桌面腿的预览（M47.12）：**统一走桌面坐标截屏**——桌面元素没有「页面」可拍，
        # 但窗口/控件截图恰恰是它原生该有的样子（红框换算还是1:1，见 screen_shot）。
        # 默认停在「精准定位」（即这一屏字段表），与browser 腿同一口径。
        self.desktop_tabs = QTabWidget()
        self.preview_shot = PreviewShot()
        self.desktop_tabs.addTab(self.preview_shot, "预览")
        locate = QWidget()
        locate_layout = QVBoxLayout(locate)
        locate_layout.setContentsMargins(4, 4, 4, 4)
        locate_layout.addLayout(layout)
        self.desktop_tabs.addTab(locate, "精准定位")
        self.desktop_tabs.setCurrentIndex(1)
        self._desktop_tab_host = self
        self._build_locator_path_tree(locate_layout)
        self._build_anchor_view(locate_layout)
        # 桌面截图通道（注入式）：app 侧按 metadata 里的 windowHandle + 元素矩形接上；
        # 未注入时页签仍在，只是停在「尚未获取预览截图」（同 browser 腿口径）。
        self._shot_css = None
        self._shot_seq = 0
        self._preview_signal = _PreviewDone()
        self._preview_signal.done.connect(self._on_preview_done)
        self.desktop_tabs.currentChanged.connect(self._on_tab_changed)
        self._on_backend_changed(self.backend_combo.currentText())

    # -- 桌面：祖先链节点树（D1，M50 起可编辑） -------------------------------

    def _build_locator_path_tree(self, layout: QVBoxLayout) -> None:
        """祖先链编辑（D1）：按 ``locator.path`` 逐级列出，**可增删级 + 改级内键**。

        ``path`` 为空/缺失时仍建控件（M48 是「没有就不建」）——M50 起用户要能**新建**
        祖先链（捕获漏了、或旧元素没有），没有入口就无从开始。故永远给出表格 + 按钮。

        **重拼规则不新发明**：每级至少一个键、全空级丢弃，逐字复用
        ``model.desktop.prune_locator_steps``（与捕获侧 ``_locator_step_for`` 共用的同一份）。
        M48 的顾虑是「编辑器另立一套『一级长什么样』的权威」；M50 用**共享纯函数**回应，
        而不是在编辑器里再抄一遍规则。

        产物即时回写 ``self._structured["path"]``（空 ⇒ 移除该键，等价「不收窄」），
        再走既有的 ``revalidate()``（判据权威仍是模型）。
        """
        label = QLabel("控件祖先链（locator.path，本级到目标的**容器**，不含根窗口与目标自身）")
        label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(label)

        table = QTableWidget(0, len(LOCATOR_STEP_KEYS))
        table.setHorizontalHeaderLabels(list(LOCATOR_STEP_KEYS))
        table.verticalHeader().setVisible(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # 可编辑（M48 是 NoEditTriggers）；双击单元格即改。
        table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        for step in self._structured.get("path") or []:
            step = _dict_or_empty(step)
            table.insertRow(table.rowCount())
            for col, key in enumerate(LOCATOR_STEP_KEYS):
                table.setItem(table.rowCount() - 1, col, QTableWidgetItem(str(step.get(key) or "")))
        table.itemChanged.connect(self._on_path_table_changed)
        layout.addWidget(table)
        self.path_table = table

        buttons = QHBoxLayout()
        for text, handler in (
            ("加一级", self._on_path_add_level),
            ("删除选中级", self._on_path_remove_level),
            ("上移", lambda: self._on_path_move(-1)),
            ("下移", lambda: self._on_path_move(1)),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.path_hint = QLabel("")
        self.path_hint.setWordWrap(True)
        self.path_hint.setStyleSheet(f"color: {WARNING};")
        layout.addWidget(self.path_hint)
        self._sync_path_table_rows()

    def _path_table_steps(self) -> list[dict]:
        """把表格当前内容读成 path 草稿（**原样**，不做裁剪——裁剪交给共享纯函数）。"""
        table = self.path_table
        steps: list[dict] = []
        for row in range(table.rowCount()):
            step: dict = {}
            for col, key in enumerate(LOCATOR_STEP_KEYS):
                item = table.item(row, col)
                value = item.text() if item is not None else ""
                if value.strip():
                    step[key] = value.strip()
            steps.append(step)
        return steps

    def _on_path_table_changed(self, *args: Any) -> None:
        del args
        if self._path_table_guard:
            return
        self._commit_path_steps()

    def _commit_path_steps(self) -> None:
        """把表格草稿经共享纯函数裁剪后写回 ``self._structured["path"]``。"""
        raw = self._path_table_steps()
        pruned = prune_locator_steps(raw)
        dropped = len(raw) - len(pruned)
        if pruned:
            self._structured["path"] = pruned
        else:
            self._structured.pop("path", None)
        self._sync_path_table_rows()
        self.path_hint.setText(
            f"有 {dropped} 级没有任何键，保存时会丢弃（每级至少要一个键）。" if dropped else ""
        )
        self.revalidate()

    def _sync_path_table_rows(self) -> None:
        """行标题显示序号 + 高亮「会被丢弃的空级」（整表重绘时挡住信号防回环）。"""
        table = self.path_table
        self._path_table_guard = True
        try:
            for row in range(table.rowCount()):
                empty = not any(
                    (table.item(row, col).text() if table.item(row, col) else "").strip()
                    for col in range(len(LOCATOR_STEP_KEYS))
                )
                header = QTableWidgetItem(f"{row + 1}（空级·将丢弃）" if empty else str(row + 1))
                table.setVerticalHeaderItem(row, header)
        finally:
            self._path_table_guard = False

    def _focus_row(self, row: int) -> None:
        if 0 <= row < self.path_table.rowCount():
            self.path_table.setCurrentCell(row, 0)

    def _on_path_add_level(self) -> None:
        table = self.path_table
        row = table.currentRow()
        insert_at = row + 1 if row >= 0 else table.rowCount()
        table.insertRow(insert_at)
        for col in range(len(LOCATOR_STEP_KEYS)):
            table.setItem(insert_at, col, QTableWidgetItem(""))
        self._focus_row(insert_at)
        self._commit_path_steps()

    def _on_path_remove_level(self) -> None:
        table = self.path_table
        row = table.currentRow()
        if row < 0:
            self.path_hint.setText("先选中要删除的那一级。")
            return
        table.removeRow(row)
        self._commit_path_steps()

    def _on_path_move(self, delta: int) -> None:
        """上移/下移选中级——祖先链是**有序**的，顺序错了收窄就会走错分支。"""
        table = self.path_table
        row = table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < table.rowCount()):
            return
        values = self._row_values(row)
        other = self._row_values(target)
        self._path_table_guard = True
        try:
            self._write_row(row, other)
            self._write_row(target, values)
        finally:
            self._path_table_guard = False
        self._focus_row(target)
        self._commit_path_steps()

    def _row_values(self, row: int) -> list[str]:
        return [
            (self.path_table.item(row, col).text() if self.path_table.item(row, col) else "")
            for col in range(len(LOCATOR_STEP_KEYS))
        ]

    def _write_row(self, row: int, values: list[str]) -> None:
        for col, value in enumerate(values):
            item = self.path_table.item(row, col)
            if item is None:
                self.path_table.setItem(row, col, QTableWidgetItem(value))
            else:
                item.setText(value)

    def _reload_path_table(self) -> None:
        """按 ``self._structured["path"]`` 重绘表格（外部改动后同步，如锚点清空重建）。"""
        self._path_table_guard = True
        try:
            self.path_table.setRowCount(0)
            for step in self._structured.get("path") or []:
                step = _dict_or_empty(step)
                row = self.path_table.rowCount()
                self.path_table.insertRow(row)
                for col, key in enumerate(LOCATOR_STEP_KEYS):
                    self.path_table.setItem(row, col, QTableWidgetItem(str(step.get(key) or "")))
        finally:
            self._path_table_guard = False
        self._sync_path_table_rows()


    # -- 桌面：锚点（D3，M50 起可编辑） ---------------------------------------

    def _build_anchor_view(self, layout: QVBoxLayout) -> None:
        """锚点编辑（D3）：内嵌**一层** locator 表单（M48 是只读说明）。

        ``anchor`` = ``{"locator": <DesktopLocator>}``，运行目标前先解析它、找不到报
        ``ANCHOR_NOT_FOUND``。M48 的顾虑是「锚点自己也带锚点怎么办」——**模型层已解决**
        （``DesktopLocator`` 校验 anchor 不可嵌套），故这里**只给一层**：内嵌表单本身
        没有「再加锚点」的入口，用户天然构造不出嵌套，不需要界面再立一套等价规则。

        「启用锚点」勾选框：勾上才走表单；取消即从 ``self._structured`` 移除 anchor
        （回到「无锚点」）。表单字段复用标量字段表（与主 locator 同一份
        ``LOCATOR_FIELDS_BY_BACKEND``），但**只给后端字段子集**——锚点 locator 与目标
        locator 走同一个后端。
        """
        self.anchor_box = QCheckBox("启用锚点（运行前先确认它存在，找不到报 ANCHOR_NOT_FOUND）")
        self.anchor_box.setChecked(bool(self._structured.get("anchor")))
        layout.addWidget(self.anchor_box)

        self._anchor_container = QWidget()
        anchor_form = QFormLayout(self._anchor_container)
        anchor_form.setContentsMargins(16, 0, 0, 0)  # 缩进一级，视觉上归属锚点
        inner = _dict_or_empty(_dict_or_empty(self._structured.get("anchor")).get("locator"))

        self.anchor_backend_combo = QComboBox()
        self.anchor_backend_combo.addItems(list(BACKENDS))
        backend = str(inner.get("backend") or self.backend_combo.currentText() or "uia")
        if backend not in BACKENDS:
            self.anchor_backend_combo.addItem(backend)
        self.anchor_backend_combo.setCurrentText(backend)
        anchor_form.addRow("backend", self.anchor_backend_combo)

        self.anchor_edits: dict[str, QLineEdit] = {}
        for key in ALL_FIELD_KEYS:
            edit = QLineEdit(str(inner.get(key) or ""))
            edit.setPlaceholderText(field_hint(key))
            edit.textChanged.connect(self._commit_anchor)
            anchor_form.addRow(key, edit)
            self.anchor_edits[key] = edit
        layout.addWidget(self._anchor_container)

        self.anchor_box.toggled.connect(self._on_anchor_toggled)
        self.anchor_backend_combo.currentTextChanged.connect(self._on_anchor_backend_changed)
        self._on_anchor_backend_changed(self.anchor_backend_combo.currentText())
        self._on_anchor_toggled(self.anchor_box.isChecked())

    def _on_anchor_toggled(self, on: bool) -> None:
        self._anchor_container.setVisible(on)
        self.anchor_backend_combo.setEnabled(on)
        for edit in self.anchor_edits.values():
            edit.setEnabled(on)
        self._commit_anchor()

    def _on_anchor_backend_changed(self, backend: str) -> None:
        """锚点后端字段可见性——与主 locator 同规则（各后端只显示自己的字段）。"""
        visible = {key for key, _kind, _hint in fields_for(backend)}
        for key, edit in self.anchor_edits.items():
            edit.setVisible(key in visible)
        self._commit_anchor()

    def _commit_anchor(self, *args: Any) -> None:
        """把锚点表单收成 ``{"locator": {...}}`` 写回 ``self._structured``（空/未启用 ⇒ 移除）。"""
        del args
        if not self.anchor_box.isChecked():
            self._structured.pop("anchor", None)
            self.revalidate()
            return
        backend = self.anchor_backend_combo.currentText()
        values = {
            key: edit.text()
            for key, edit in self.anchor_edits.items()
            if key in {k for k, _kind, _hint in fields_for(backend)}
        }
        try:
            locator = compose_locator(backend, values)
        except LocatorFieldError:
            locator = {"backend": backend}  # 非法（如 controlId 非整数）交给 revalidate 报
        self._structured["anchor"] = {"locator": locator}
        self.revalidate()

    def _on_backend_changed(self, backend: str) -> None:
        """切换 backend：只显示该后端的字段行，并提示被挡掉的死字段。"""
        visible = {key for key, _kind, _hint in fields_for(backend)}
        for key, widgets in self.field_rows.items():
            for widget in widgets:
                widget.setVisible(key in visible)
        inert = inert_locator_keys(self._original_locator)
        if inert:
            self.field_hint.setText(
                "原 locator 里的 " + "、".join(inert)
                + " 当前 backend 不消费（执行器读不到），保存会移除。"
            )
        else:
            self.field_hint.setText("")
        self.revalidate()

    def _desktop_values(self) -> dict[str, str]:
        """只取**当前 backend** 的已勾字段——跨后端字段一律不组装。"""
        fields = {
            key for key, _kind, _hint in fields_for(self.backend_combo.currentText())
        }
        return {
            key: (edit.text() if self.field_boxes[key].isChecked() else "")
            for key, edit in self.field_edits.items()
            if key in fields
        }

    def _compose(self) -> dict[str, Any]:
        return compose_locator(
            self.backend_combo.currentText(),
            self._desktop_values(),
            structured=self._structured,
        )

    def problems(self) -> list[str]:
        """当前编辑结果的结构问题（空列表 = 可保存）。"""
        if self._kind == "browser":
            return css_problems(self.css_edit.text())
        try:
            locator = self._compose()
        except LocatorFieldError as exc:
            return [str(exc)]
        return locator_problems(locator)

    #: 「确定」是否应被拦住（有问题才算；提示 notice 不算问题）。
    blocked: bool = False

    def revalidate(self, *args: Any, notice: str | None = None) -> None:
        """每次改动都重判一次；问题就地显示（不弹窗），与确认框同风格。"""
        del args
        problems = self.problems()
        parts = list(problems)
        if notice:
            parts.append(notice)
        self.info_label.setText("\n".join(parts))
        self.info_label.setStyleSheet(
            f"color: {DANGER};" if problems else f"color: {WARNING};"
        )
        self.blocked = bool(problems)
        self.changed.emit()

    def result_document(self) -> dict[str, Any]:
        """编辑结果（ElementDescriptor 形状）。仅在 Accepted 后调用。"""
        selector: dict[str, Any] = dict(self._extras)
        if self._kind == "browser":
            selector["css"] = self.css_edit.text().strip()
            if self._candidates:
                selector["candidates"] = [dict(item) for item in self._candidates]
        else:
            selector["locator"] = self._compose()
        return {
            "kind": self._kind,
            "selector": selector,
            "verifyCount": self._verify_count,
            "metadata": self._document.get("metadata") or {},
        }


class ElementEditorDialog(QDialog):
    """元素编辑器对话框（元素库的「编辑」入口）。

    编辑能力全部在 :class:`ElementEditorForm` 里；本类只提供外壳（标题 + 编辑区 +
    保存/取消）与一条判定：**有结构错误就不许保存**。

    ``__getattr__`` 把对编辑控件的访问转发给 form：调用方（与判据）关心的是
    「编辑器能改什么」，不是「控件挂在哪个对象上」。这样「确认框」与「编辑器」
    共用一份编辑逻辑时，两边的用法不会因为控件搬了家而分叉。
    """

    def __init__(
        self,
        document: dict[str, Any],
        *,
        name: str,
        preview_css: Callable[[str], dict] | None = None,
        clear_preview_css: Callable[[], dict] | None = None,
        shot_css: Callable[..., dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑元素 · {name}")
        self.setMinimumWidth(_DIALOG_MIN_WIDTH)
        # 默认开大一点：节点树 + 属性表是主工作区，窄窗会把表格压成三行；
        # 用户调过的尺寸下次沿用（M49 P1-1，键见 persist 模块），但宽度有下限。
        self.resize(*_open_wide_enough(load_size(_DIALOG_SIZE_KEY)))

        layout = QVBoxLayout(self)
        header = "浏览器元素" if document.get("kind") == "browser" else "桌面元素"
        title = QLabel(f"{name}（{header}）")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.form = ElementEditorForm(document, parent=self)
        layout.addWidget(self.form)
        # 编辑中预览（M48）：browser 元素且调用方注入了通道才启用；关窗（含取消）
        # 一律发 clear 收走页面上的黄框——预览框不能陪对话框一起「留在页面上」。
        # ``shot_css`` 是截图通道（M47.12）：切到「预览」页签才拍，不随防抖预跑。
        #
        # **两条腿都要接上截图**（M47.12 起桌面腿也有「预览」页签）：browser 走
        # enable_live_preview 的第三参，desktop 走 enable_shot_channel——分开的理由
        # 见那两个方法的 docstring（桌面没有「页面」可高亮、没有 css 可防抖）。
        if document.get("kind") == "browser" and preview_css is not None:
            self.form.enable_live_preview(preview_css, clear_preview_css, shot_css)
            self.finished.connect(self.form.shutdown_preview)
        elif shot_css is not None:
            self.form.enable_shot_channel(shot_css)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def __getattr__(self, name: str) -> Any:
        """编辑控件与编辑逻辑都住在 form 里（见类 docstring）。"""
        form = self.__dict__.get("form")
        if form is not None and hasattr(form, name):
            return getattr(form, name)
        raise AttributeError(name)

    def done(self, result: int) -> None:  # noqa: N802 (Qt naming)
        """关窗（保存/取消/点 X 都走这里）时记住用户调过的尺寸。"""
        save_size(_DIALOG_SIZE_KEY, (self.width(), self.height()))
        super().done(result)

    def accept(self) -> None:
        self.form.revalidate()
        if self.form.blocked:
            return  # 错误已就地展示，保持对话框打开让用户改
        super().accept()

    def result_document(self) -> dict[str, Any]:
        """编辑结果（ElementDescriptor 形状）。仅在 Accepted 后调用。"""
        return self.form.result_document()
