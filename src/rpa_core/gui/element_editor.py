"""元素编辑器（M39 ③-1，离线版）。

影刀的元素编辑器活在**捕获会话里**：左侧 DOM 节点树、右侧属性勾选、活体校验。我们没有
活页面就做不出节点树与活体验证（见 ``.harness/tasks/M39-element-editor.md`` 的 ③-2）。
但用户真正卡住的那一步并不需要活页面：

    **不写 CSS、不写 JSON，也能选出一个定位方案。**

v1 只做这件事，全部基于**已捕获的数据**：

1. **候选可选** —— 捕获时收集的备选定位（带捕获时**实测**命中数）点一下即成为主定位。
   提升时旧主定位**按序放回候选队首**（它的实测命中数就是 ``verifyCount``），因此一次
   提升不丢任何备选，也不是单向操作。
2. **桌面 locator 字段化** —— 勾选框代替手写 JSON（勾上写进 locator、取消即移除）；
   字段集**按 backend 分**，见下。
3. **就地结构校验** —— 每次改动都拿**模型**判一次，错误显示在对话框内。界面**不另立
   一套规则**：判据的权威只有 ``DesktopLocator`` / ``selector_errors``，两套规则必然漂移。

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

import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QObject, Qt, QTimer, Signal
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
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 每个 backend **真正被消费**的 locator 字段：``(键, 值类型, 说明)``。
# 值类型只有 "text" / "int"；顺序即界面顺序。
LOCATOR_FIELDS_BY_BACKEND: dict[str, tuple[tuple[str, str, str], ...]] = {
    "uia": (
        ("controlType", "text", "控件类型，如 Button / Edit / List"),
        ("automationId", "text", "最稳，等同网页的 id"),
        ("name", "text", "控件显示名（执行器按 title 匹配）"),
    ),
    "win32": (
        ("title", "text", "窗口文本（等值）"),
        ("className", "text", "类名等值匹配（与 classNameRe 互斥）"),
        ("classNameRe", "text", "类名正则匹配（与 className 互斥）"),
        ("controlId", "int", "控件序号——**每次进程启动都变**，只适合短期脚本"),
        ("foundIndex", "int", "命中多个时取第几个（从 0 开始）"),
    ),
}

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
    key
    for fields in LOCATOR_FIELDS_BY_BACKEND.values()
    for key, _kind, _hint in fields
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
    """
    known = {key for key, _kind, _hint in fields_for(str(locator.get("backend")))}
    declared = set(declared_locator_keys())
    return sorted(
        key
        for key in locator
        if key != "backend" and key in declared and key not in known
    )


def compose_locator(backend: str, values: dict[str, str]) -> dict[str, Any]:
    """按「勾选的字段」组装 locator：**值为空即不出现**那个键。

    界面用「值空 = 不勾」表达，所以组装层不需要单独的勾选状态——少一个可能与界面
    不同步的状态源。只组装该 backend 的字端表内的字段（杜绝跨后端死字段）。
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
    return locator


def split_locator(locator: dict[str, Any]) -> dict[str, str]:
    """把 locator 摊成界面用的字符串值（只取该 backend 的字段）。"""
    values: dict[str, str] = {}
    for key, _kind, _hint in fields_for(str(locator.get("backend"))):
        if key not in locator or locator[key] is None:
            continue
        values[key] = str(locator[key])
    return values


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


def promotable(candidate: dict[str, Any]) -> bool:
    """该候选能否提升为主定位。

    只有**捕获时实测过命中数**（``matchedCount`` 为正整数）的候选才能提升：提升要把
    它的实测值写成新的 ``verifyCount``，没有实测值就只能猜一个——而 ``verifyCount``
    显示给用户的意义就是「这个是实测的」。宁可不给这个按钮，也不给一个假的数。

    实践中不会有候选缺这个值：``content.js`` 的 ``candidatesFor`` 只 push 命中数 ≥1 的项
    且总是带上 ``matchedCount``。缺值只会出现在手改过的旧文档上。
    """
    matched = candidate.get("matchedCount")
    return isinstance(matched, int) and not isinstance(matched, bool) and matched >= 1


def promote_candidate(
    candidates: list[dict[str, Any]],
    index: int,
    *,
    current_css: str,
    current_count: int,
) -> tuple[list[dict[str, Any]], str, int, str | None]:
    """把 ``candidates[index]`` 提升为主定位。

    返回 ``(新候选列表, 新主 css, 新 verifyCount, 提示或 None)``。

    旧主定位**按序放回候选队首**（``candidates`` 的顺序就是回退优先级，而旧主定位是
    原本最强的那个），其 ``matchedCount`` 取 ``current_count``——``verifyCount`` 的
    定义就是「主选择器捕获时的命中数」，所以这个回填是实测值而非估计值。
    ``current_count < 1`` 时无法表示成合法候选（契约要求 ``matchedCount >= 1``），
    此时不塞回并返回一条提示，而不是悄悄丢掉它。
    """
    if not 0 <= index < len(candidates):
        raise IndexError(index)
    remaining = [dict(item) for item in candidates]
    promoted = remaining.pop(index)
    note: str | None = None
    if current_css and current_css != promoted.get("selector"):
        if current_count >= 1:
            remaining.insert(
                0,
                {"kind": "css", "selector": current_css, "matchedCount": current_count},
            )
        else:
            note = (
                f"旧主定位 {current_css} 的捕获命中数为 {current_count}，"
                "无法作为候选保留（候选要求命中数 ≥1）"
            )
    return remaining, str(promoted.get("selector") or ""), int(
        promoted["matchedCount"]
    ), note


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


def _candidate_label(candidate: dict[str, Any]) -> str:
    """候选列表行：``[kind] selector · 命中 N``（不唯一要标出来）。"""
    matched = candidate.get("matchedCount")
    if isinstance(matched, int) and not isinstance(matched, bool):
        count_text = f"命中 {matched}" + ("（不唯一）" if matched > 1 else "")
    else:
        count_text = "命中未实测"
    kind = candidate.get("kind") or "?"
    return f"[{kind}] {candidate.get('selector') or ''} · {count_text}"


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
        self.info_label.setStyleSheet("color: #cf222e;")

        if self._kind == "browser":
            self._build_browser(layout)
        else:
            self._build_desktop(layout, raw_selector)

        layout.addWidget(self.info_label)
        self.revalidate()

    # -- 浏览器 --------------------------------------------------------------

    def _build_browser(self, layout: QVBoxLayout) -> None:
        selector = _dict_or_empty(self._document.get("selector"))
        form = QFormLayout()
        self.css_edit = QLineEdit(str(selector.get("css") or ""))
        self.css_edit.textChanged.connect(self.revalidate)
        form.addRow("主选择器（css）", self.css_edit)
        layout.addLayout(form)

        # 编辑中预览（M48/C3）：改动 css 后 500ms 防抖，页面上驻留高亮当前命中。
        # 通道回调由调用方注入（`enable_live_preview`，app 侧只对 browser 元素接）；
        # 未注入时标签隐藏、计时器永不启动——桌面元素没有「页面」可高亮。
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet("color: #64707d;")
        self.preview_label.hide()
        layout.addWidget(self.preview_label)
        self._preview_css: Callable[[str], dict] | None = None
        self._clear_preview_css: Callable[[], dict] | None = None
        self._preview_seq = 0
        self._preview_signal = _PreviewDone()
        self._preview_signal.done.connect(self._on_preview_done)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._run_preview)
        self.css_edit.textChanged.connect(lambda _text: self._on_css_changed())

        self.candidates_label = QLabel(
            f"捕获时的备选定位 {len(self._candidates)} 条"
            "（运行期主选择器失效时按序回退）"
            if self._candidates
            else "捕获时没有收集到备选定位"
        )
        self.candidates_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.candidates_label)

        self.candidate_list = QListWidget()
        self.candidate_list.addItems(
            [_candidate_label(item) for item in self._candidates]
        )
        self.candidate_list.currentRowChanged.connect(lambda _row: self._sync_promote())
        self.candidate_list.itemDoubleClicked.connect(lambda _item: self._promote())
        layout.addWidget(self.candidate_list)

        promote_row = QHBoxLayout()
        self.promote_button = QPushButton("设为主定位")
        self.promote_button.clicked.connect(self._promote)
        promote_row.addWidget(self.promote_button)
        promote_row.addStretch(1)
        layout.addLayout(promote_row)
        self._sync_promote()
        self._build_path_tree(layout)

    # -- 浏览器：节点树（勾层级 → 拼回主选择器） -----------------------------

    def _build_path_tree(self, layout: QVBoxLayout) -> None:
        """节点树：按捕获回传的祖先链（``selector.path``）逐级勾选。

        **单向**（树 → 主选择器）。``css_edit`` 是唯一落盘口径；树是「从捕获路径
        重新拼选择器」的入口。不做双向同步是有意的：把 css 解析回层级不可靠
        （手改的 css / 候选提升后的 css 都不出自这条路径），硬做双向就是立第二套
        口径、迟早互相改写。所以树只承诺一件事——**再次勾选，就按所选层级重写
        主选择器**；两个入口写同一个字段，后动手的赢。
        """
        if not self._path:
            return  # 老元素没有 path：不建树也不摆一个空壳
        self.path_label = QLabel(
            "节点路径（勾选参与定位的层级；勾选会重写主选择器）"
        )
        self.path_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.path_label)

        self.path_list = QListWidget()
        self.path_list.setMinimumHeight(150)
        self._composing_path = False
        for index, entry in enumerate(self._path):
            item = QListWidgetItem(str(entry["fragment"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            extra = entry.get("id") or (entry.get("classes") or "")
            item.setToolTip(
                f"层级 {index + 1}/{len(self._path)} · tag={entry.get('tag') or '?'}"
                + (f" · {extra}" if extra else "")
            )
            self.path_list.addItem(item)
        self.path_list.itemChanged.connect(self._on_path_item_changed)
        layout.addWidget(self.path_list)
        self._build_attr_table(layout)
        self._sync_path_checks_from_css()
        # 默认选中目标所在层（末级）：属性表打开即有内容可看
        self.path_list.setCurrentRow(len(self._path) - 1)

    # -- 浏览器：属性表（A1，勾属性/改匹配方式 → 重写该层 fragment） ----------

    def _build_attr_table(self, layout: QVBoxLayout) -> None:
        """属性表：对**树中选中的一层**逐属性勾选、改匹配方式。

        节点树回答「参与定位的是哪几级」，属性表回答「这一级里哪些属性参与、
        精确还是模糊」——影刀编辑器右侧三列表（属性名 | 匹配方式 | 属性值）
        的离线版。「包含」编译成 CSS 属性选择器（``[class*="…"]``），
        消费方照旧是执行器的 ``querySelectorAll``，不需要动执行器。
        落盘口径仍唯一：所有编辑最终都只是重写 ``css_edit``（经
        ``_on_path_item_changed`` 的同一条组装路径）。
        """
        self._composing_table = False
        self._attr_rows: list[dict[str, Any]] = []
        self.attr_label = QLabel(
            "属性（勾选参与定位；匹配方式「包含」= 属性值出现即可，"
            "改动会重写主选择器）"
        )
        self.attr_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.attr_label)

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
        layout.addWidget(self.attr_table)
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

    def _sync_promote(self) -> None:
        row = self.candidate_list.currentRow()
        ok = 0 <= row < len(self._candidates) and promotable(self._candidates[row])
        self.promote_button.setEnabled(ok)
        if 0 <= row < len(self._candidates) and not promotable(
            self._candidates[row]
        ):
            self.promote_button.setToolTip(
                "该候选捕获时没实测到命中数，无法安全提升（verifyCount 必须是实测值）"
            )
        else:
            self.promote_button.setToolTip("把选中的候选设为主定位")

    def _promote(self) -> None:
        row = self.candidate_list.currentRow()
        if not (0 <= row < len(self._candidates)) or not promotable(
            self._candidates[row]
        ):
            return
        kept, css, count, note = promote_candidate(
            self._candidates,
            row,
            current_css=self.css_edit.text().strip(),
            current_count=self._verify_count,
        )
        self._candidates = kept
        self._verify_count = count
        self.css_edit.setText(css)
        # R2（M44 残留）：提升会改主 css，树的勾选态必须**立刻**按新 css 回读——
        # 此前只在建树时做一次，提升后树还停在旧勾选，下一次勾选才会重写，
        # 中间态里「树显示的层级」与「主选择器实际是哪条」是两回事。
        self._sync_path_checks_from_css()
        self.candidate_list.clear()
        self.candidate_list.addItems(
            [_candidate_label(item) for item in self._candidates]
        )
        self.candidates_label.setText(
            f"捕获时的备选定位 {len(self._candidates)} 条"
            "（运行期主选择器失效时按序回退）"
            if self._candidates
            else "捕获时没有收集到备选定位"
        )
        self.revalidate(notice=note)

    # -- 浏览器：编辑中预览（M48/C3，通道回调注入式） -------------------------

    def enable_live_preview(
        self,
        preview: Callable[[str], dict],
        clear: Callable[[], dict],
    ) -> None:
        """接上预览通道：css 每次改动后防抖驻留高亮，收场清场由 ``shutdown_preview``。

        桌面元素没有「页面」可高亮，不注入回调即整体不生效（标签都不出现）。
        """
        if not hasattr(self, "css_edit"):
            return  # 桌面表单没有主 css 行，预览无从谈起
        self._preview_css = preview
        self._clear_preview_css = clear
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
            if self._clear_preview_css is not None:
                self._preview_seq += 1
                self._dispatch(self._clear_preview_css, ())
            return
        self.preview_label.setText("预览中…")
        self._preview_seq += 1
        self._dispatch(self._preview_css, (css,))

    def _dispatch(self, callback: Callable[..., dict], args: tuple) -> None:
        seq = self._preview_seq

        def work() -> None:
            try:
                result = dict(callback(*args))
            except Exception as exc:  # noqa: BLE001 - 通道故障也要落到标签上
                result = {"error": f"预览通道异常：{exc}"}
            kind = "clear" if callback is self._clear_preview_css else "preview"
            payload = {"kind": kind, "seq": seq, **result}
            try:
                self._preview_signal.done.emit(payload)
            except RuntimeError:
                pass  # 表单已随对话框销毁：无处展示，daemon 线程自灭

        threading.Thread(target=work, daemon=True).start()

    def _on_preview_done(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        if data.get("kind") == "clear":
            return  # 清场无观感，静默即可
        if data.get("seq") != self._preview_seq:
            return  # 陈旧结果：用户已改了下一轮，覆盖反而回退显示
        if data.get("error"):
            self.preview_label.setText(f"预览失败：{data['error']}")
            self.preview_label.setStyleSheet("color: #cf222e;")
            return
        count = data.get("count")
        if count == 1:
            text, color = "预览：命中 1 个（页面上已黄框高亮）", "#1a7f37"
        elif isinstance(count, int) and not isinstance(count, bool) and count > 1:
            text, color = f"预览：命中 {count} 个（超过 1 个不唯一）", "#cf222e"
        else:
            text, color = "预览：命中 0 个（页面上找不到该选择器）", "#cf222e"
        self.preview_label.setText(text)
        self.preview_label.setStyleSheet(f"color: {color};")

    # -- 桌面 ----------------------------------------------------------------

    def _build_desktop(self, layout: QVBoxLayout, raw_selector: dict) -> None:
        locator = _dict_or_empty(raw_selector.get("locator"))
        self._original_locator = locator
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
        self.field_hint.setStyleSheet("color: #64707d;")
        layout.addWidget(self.field_hint)

        hint = QLabel(
            "勾选即写入 locator，取消即移除；值为空等于不勾。"
            "字段集按 backend 分：uia 与 win32 的执行器读取**不同的** locator 字段，"
            "提供对面后端的字段只会产出「写了没人读」的死字段。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64707d;")
        layout.addWidget(hint)
        self._on_backend_changed(self.backend_combo.currentText())

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
        return compose_locator(self.backend_combo.currentText(), self._desktop_values())

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
            "color: #cf222e;" if problems else "color: #9a6700;"
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑元素 · {name}")
        self.setMinimumWidth(560)
        # 默认开大一点：节点树 + 属性表是主工作区，窄窗会把表格压成三行
        self.resize(680, 720)

        layout = QVBoxLayout(self)
        header = "浏览器元素" if document.get("kind") == "browser" else "桌面元素"
        title = QLabel(f"{name}（{header}）")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.form = ElementEditorForm(document, parent=self)
        layout.addWidget(self.form)
        # 编辑中预览（M48）：browser 元素且调用方注入了通道才启用；关窗（含取消）
        # 一律发 clear 收走页面上的黄框——预览框不能陪对话框一起「留在页面上」。
        if document.get("kind") == "browser" and preview_css is not None:
            self.form.enable_live_preview(preview_css, clear_preview_css)
            self.finished.connect(self.form.shutdown_preview)

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

    def accept(self) -> None:
        self.form.revalidate()
        if self.form.blocked:
            return  # 错误已就地展示，保持对话框打开让用户改
        super().accept()

    def result_document(self) -> dict[str, Any]:
        """编辑结果（ElementDescriptor 形状）。仅在 Accepted 后调用。"""
        return self.form.result_document()
