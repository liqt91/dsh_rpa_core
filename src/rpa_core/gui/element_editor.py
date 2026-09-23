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
- **DOM 节点树 / 活体校验**：见 ③-2。**注意**：活体校验的能力其实已经存在——内容脚本的
  动作路径里就有 ``document.querySelectorAll(selector).length`` 与遮挡/可见性预检，
  缺的不是能力而是**通道**：GUI 的 ``_capture_element`` 在 ``work()`` 的 finally 里
  ``session.close()``，即对话框弹出前捕获会话已撤防并关掉 bridge 通道。所以 ③-2 的
  第一件事是定「会话在对话框期间保持 arm」的生命周期改造，不是写查询代码。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
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


def _candidate_label(candidate: dict[str, Any]) -> str:
    """候选列表行：``[kind] selector · 命中 N``（不唯一要标出来）。"""
    matched = candidate.get("matchedCount")
    if isinstance(matched, int) and not isinstance(matched, bool):
        count_text = f"命中 {matched}" + ("（不唯一）" if matched > 1 else "")
    else:
        count_text = "命中未实测"
    kind = candidate.get("kind") or "?"
    return f"[{kind}] {candidate.get('selector') or ''} · {count_text}"


class ElementEditorDialog(QDialog):
    """元素编辑器：浏览器走「主选择器 + 候选可选」，桌面走「locator 字段勾选」。

    判据：``result_document()`` 产出的文档必须能被 ``validate_element_document`` +
    ``selector_errors`` 接受——保存按钮的启用/拒绝状态**由同一套校验决定**，
    编辑器不承诺任何自己校验不出的东西。
    """

    def __init__(
        self,
        document: dict[str, Any],
        *,
        name: str,
        parent: QWidget | None = None,
    ) -> None:
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
        self.setWindowTitle(f"编辑元素 · {name}")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        header = "浏览器元素" if self._kind == "browser" else "桌面元素"
        title = QLabel(f"{name}（{header}）")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._revalidate()

    # -- 浏览器 --------------------------------------------------------------

    def _build_browser(self, layout: QVBoxLayout) -> None:
        selector = _dict_or_empty(self._document.get("selector"))
        form = QFormLayout()
        self.css_edit = QLineEdit(str(selector.get("css") or ""))
        self.css_edit.textChanged.connect(self._revalidate)
        form.addRow("主选择器（css）", self.css_edit)
        layout.addLayout(form)

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
        self._revalidate(notice=note)

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
            box.toggled.connect(self._revalidate)
            edit.textChanged.connect(self._revalidate)
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
        self._revalidate()

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

    def _problems(self) -> list[str]:
        if self._kind == "browser":
            return css_problems(self.css_edit.text())
        try:
            locator = self._compose()
        except LocatorFieldError as exc:
            return [str(exc)]
        return locator_problems(locator)

    def _revalidate(self, *args: Any, notice: str | None = None) -> None:
        """每次改动都重判一次；问题就地显示（不弹窗），与确认框同风格。"""
        del args
        problems = self._problems()
        parts = list(problems)
        if notice:
            parts.append(notice)
        self.info_label.setText("\n".join(parts))
        self.info_label.setStyleSheet(
            "color: #cf222e;" if problems else "color: #9a6700;"
        )
        # 提示（notice）不算问题，不该拦保存；问题才算
        self._blocked = bool(problems)

    def accept(self) -> None:
        self._revalidate()
        if getattr(self, "_blocked", False):
            return  # 错误已就地展示，保持对话框打开让用户改
        super().accept()

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
