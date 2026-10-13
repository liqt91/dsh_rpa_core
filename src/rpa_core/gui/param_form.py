"""指令参数表单（GUI 切片 3）。

把 ``CommandManifest.input_schema``（JSON Schema dict）渲染成原生控件表单，
并支持把编辑结果收回为参数 dict 写回画布卡片。

控件映射：

- ``string`` + ``enum`` → QComboBox：首项「未设置」，``x-enum-labels``
  提供中文显示文本，itemData 存实际枚举值；
- ``string`` → QLineEdit：空文本 = 未设置，placeholder 提示默认值；
  **声明了 ``x-fx`` / ``x-python``** 的字段额外挂表达式控件（见 ``_wrap_expr_row``）；
- ``integer`` / ``number`` → QLineEdit + 校验器：空文本 = 未设置；
- ``boolean`` → QCheckBox；
- ``array`` / ``object`` 等复合类型 → QLineEdit 接受 JSON 文本，
  空文本 = 未设置，非法 JSON 在收集时抛 ValueError；
- type 写成列表（如 ``["string", "null"]``）时取首个非 null 类型。
"""

from __future__ import annotations

import json
import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rpa_core.gui.i18n import field_label, load_op_labels
from rpa_core.gui.theme import (
    DANGER,
    SUCCESS,
    SURFACE_HIGHLIGHT,
    TEXT_FAINT,
    TEXT_HEADING,
    TEXT_SECONDARY,
)

# 字段条目类型标记（决定 values() 如何从控件取值）
_KIND_ENUM = "enum"
_KIND_TEXT = "text"
_KIND_INT = "integer"
_KIND_NUMBER = "number"
_KIND_BOOL = "boolean"
_KIND_JSON = "json"


class _CollapsibleSection(QWidget):
    """可折叠的参数分组区段（对齐 Web ``paramGroupSection``）。"""

    def __init__(
        self,
        title: str,
        *,
        field_count: int = 0,
        collapsed: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- header ----
        self._header = QWidget()
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(0, 4, 0, 4)
        header_layout.setSpacing(4)

        self._caret = QToolButton()
        self._caret.setText("▾" if not collapsed else "▸")
        self._caret.setFixedSize(16, 16)
        self._caret.setStyleSheet("border: none; padding: 0;")
        header_layout.addWidget(self._caret)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-weight: bold; color: {TEXT_HEADING};")
        header_layout.addWidget(title_label, 1)

        count_label = QLabel(str(field_count))
        count_label.setStyleSheet(f"color: {TEXT_FAINT};")
        header_layout.addWidget(count_label)

        header_layout.addStretch()

        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = self._toggle
        self._caret.clicked.connect(self._toggle)
        outer.addWidget(self._header)

        # ---- body ----
        self._body = QWidget()
        self._body_layout = QFormLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setLabelAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self._body)

        if collapsed:
            self._body.setVisible(False)

    def _toggle(self, *_args: object) -> None:
        hidden = self._body.isHidden()
        self._body.setVisible(hidden)
        self._caret.setText("▾" if hidden else "▸")

    @property
    def body_layout(self) -> QFormLayout:
        return self._body_layout


def _effective_type(field_schema: dict[str, Any]) -> str:
    """取字段实际类型；type 为列表时选第一个非 'null' 的候选。"""
    raw_type = field_schema.get("type", "string")
    if isinstance(raw_type, list):
        for candidate in raw_type:
            if candidate != "null":
                return candidate
        return "string"
    return raw_type


class ParamForm(QWidget):
    """按 input_schema 构建的参数表单；values() 收集当前编辑结果。

    表达式控件（对标影刀 / Web 编辑器）：字段**声明** ``x-fx`` / ``x-python`` 时，
    行内挂「左 Py 右 fx」两个按钮——``Py`` 是 Python 表达式模式开关，``fx``
    **不是开关**、点击即弹出变量列表并把变量插到光标处。两种模式**互斥**，以
    ``_expr_modes`` 为唯一真值（运行期据此走 ``resolve_tags`` 或 ``resolve``）。
    """

    def __init__(
        self,
        schema: dict[str, Any],
        args: dict[str, Any] | None = None,
        parent: QWidget | None = None,
        *,
        expr_modes: dict[str, str] | None = None,
        variable_provider: Any = None,
        manifest: Any | None = None,
        output_aliases: dict[str, str] | None = None,
        raw: dict[str, Any] | None = None,
        element_provider: Any = None,
    ) -> None:
        super().__init__(parent)
        args = dict(args or {})
        self._required: set[str] = set(schema.get("required", []))
        self._expr_modes: dict[str, str] = dict(expr_modes or {})
        self._variable_provider = variable_provider
        # (字段名, 种类, 控件)：顺序即 properties 声明顺序
        self._fields: list[tuple[str, str, QWidget]] = []
        # 声明了 default 的布尔字段（名字 → 默认值）：`values()` 靠它把「等于默认」
        # 的布尔认成「未设置」而不写进参数（详见 `values()` 里的说明）。
        self._bool_defaults: dict[str, bool] = {}
        # 表达式控件行（字段名 → (Py 按钮 | None, fx 按钮 | None, 取值编辑器)）。
        # 只有声明了 x-fx / x-python 的字段才登记——没登记的字段即使 raw 里残留旧
        # `_exprModes` 也不会被 `expr_modes()` 回传（开发期硬切、不做迁移）。
        self._expr_rows: dict[
            str, tuple[QToolButton | None, QToolButton | None, QLineEdit]
        ] = {}
        # 分组模式下的区段引用（section_name → _CollapsibleSection）
        self._sections: dict[str, _CollapsibleSection] = {}
        # 输出别名字段（outField → QLineEdit）
        self._alias_fields: dict[str, QLineEdit] = {}
        self._alias_validator = re.compile(r"^[A-Za-z_]\w*$")
        # 节点 raw（读其中的 elementRefs 作元素引用行的初值）
        self._raw: dict[str, Any] = dict(raw or {})
        # 命令 id：字段标签的「按命令覆盖」（commandFields）要用它；manifest 缺失时 None
        self._command_id: str | None = getattr(manifest, "id", None)
        # 元素库引用：element_provider 提供 (元素名, 文档) 列表；为 None 则不加选择行
        self._element_provider = element_provider
        self._element_rows: dict[str, QComboBox] = {}
        # 元素行的行标签：引用态称「操作目标」、非引用态称「元素库」（见 _apply_element_state）
        self._element_row_labels: dict[str, QLabel] = {}
        # M52 S6：引用生效时该参数**只显元素名**、值控件整行隐藏，故需留下这些把手
        self._element_picked: dict[str, QLabel] = {}
        self._element_clears: dict[str, QToolButton] = {}
        self._row_labels: dict[str, QLabel] = {}
        self._row_shown: dict[str, QWidget] = {}
        # 只记**用户显式动过**的键（值 → 元素名 / None = 清除引用）；没动过的不在内，
        # 调用方据此区分「用户接管了该参数」与「用户只是打开面板又应用」。
        self._ref_intent: dict[str, str | None] = {}

        self._form = QFormLayout(self)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignTop)

        properties = schema.get("properties", {})
        groups = schema.get("x-param-groups")
        if groups and isinstance(groups, list) and properties:
            self._build_grouped_form(properties, args, groups)
        else:
            for name, field_schema in properties.items():
                self._build_field(name, field_schema, args)

        # 输出别名区段（对齐 Web x-outputs 渲染）
        self._build_output_aliases(manifest, output_aliases or {})

        # 运行设置：超时 / 重试（对齐 Web timeout_seconds / retryCountField）
        self._timeout_field: QLineEdit | None = None
        self._retry_field: QWidget | None = None
        self._build_retry_timeout(manifest, raw_dict=(raw or {}))

    def _build_grouped_form(
        self,
        properties: dict[str, Any],
        args: dict[str, Any],
        groups: list[dict[str, Any]],
    ) -> None:
        """按 x-param-groups 分组渲染字段（对齐 Web ``paramGroupPlan`` 逻辑）。"""
        claimed: set[str] = set()
        for group in groups:
            group_fields = [
                k
                for k in (group.get("fields") or [])
                if k in properties and k not in claimed
            ]
            if not group_fields:
                continue
            claimed.update(group_fields)
            label = group.get("label") or "参数"
            # collapsed 组内有值时自动展开（对齐 Web 行为）
            has_value = any(
                args.get(k) not in (None, "") for k in group_fields
            )
            collapsed = bool(group.get("collapsed")) and not has_value
            section = _CollapsibleSection(
                label,
                field_count=len(group_fields),
                collapsed=collapsed,
            )
            self._sections[label] = section
            self._form.addRow(section)
            for k in group_fields:
                self._build_field(k, properties[k], args, target=section.body_layout)

        # 未被任何 group 声明的字段 →「其他」组
        rest = [k for k in properties if k not in claimed]
        if rest:
            section = _CollapsibleSection(
                "其他", field_count=len(rest), collapsed=False,
            )
            self._sections["其他"] = section
            self._form.addRow(section)
            for k in rest:
                self._build_field(k, properties[k], args, target=section.body_layout)

    # ---- 输出别名（对齐 Web x-outputs） ------------------------------------
    def _build_output_aliases(
        self,
        manifest: Any | None,
        existing: dict[str, str],
    ) -> None:
        """在参数表单末尾追加「输出参数」区段，为每个非 hidden 输出渲染别名输入框。"""
        x_outputs = getattr(manifest, "x_outputs", None) or {}
        visible = [
            (field, meta)
            for field, meta in x_outputs.items()
            if not (isinstance(meta, dict) and meta.get("hidden"))
        ]
        if not visible:
            return
        header = QLabel("输出参数（保存到变量，供后续指令引用）")
        header.setStyleSheet(f"font-weight: bold; color: {TEXT_HEADING}; padding-top: 8px;")
        self._form.addRow(header)
        for field, meta in visible:
            label_text = (meta.get("label") if isinstance(meta, dict) else None) or field
            edit = QLineEdit(existing.get(field, ""))
            edit.setPlaceholderText("可选，如 webpage1")
            edit.setToolTip(f"为输出 {field} 命名，后续节点可通过 ${{别名}} 引用")
            edit.textChanged.connect(lambda text, e=edit: self._style_alias_field(e))
            self._alias_fields[field] = edit
            self._form.addRow(QLabel(label_text), edit)

    def _style_alias_field(self, edit: QLineEdit) -> None:
        """别名内容不合法时红框提示（对齐 Web 别名校验）。"""
        text = edit.text().strip()
        if text and not self._alias_validator.match(text):
            edit.setStyleSheet(f"QLineEdit {{ border: 1px solid {DANGER}; }}")
        else:
            edit.setStyleSheet("")

    def output_aliases(self) -> dict[str, str]:
        """收集输出别名：仅返回非空且合法的条目。"""
        result: dict[str, str] = {}
        for field, edit in self._alias_fields.items():
            alias = edit.text().strip()
            if alias and self._alias_validator.match(alias):
                result[field] = alias
        return result

    # ---- 运行设置：超时 / 重试（对齐 Web timeout_seconds / retryCountField） ---
    def _build_retry_timeout(
        self, manifest: Any | None, raw_dict: dict[str, Any]
    ) -> None:
        """在表单末尾追加超时和重试字段（对齐 Web 编辑器行为）。"""
        if manifest is None:
            return
        properties = getattr(manifest, "input_schema", {}).get("properties", {})
        has_own_timeout = "timeoutMs" in properties

        # 超时（秒）：命令自带 timeoutMs 时隐藏引擎级超时，避免两个「超时」
        if not has_own_timeout:
            header = QLabel("运行设置")
            header.setStyleSheet(f"font-weight: bold; color: {TEXT_HEADING}; padding-top: 8px;")
            self._form.addRow(header)
            self._timeout_field = QLineEdit()
            self._timeout_field.setValidator(QDoubleValidator(0, 86400, 1))
            current_timeout = raw_dict.get("timeout_seconds")
            if current_timeout is not None:
                self._timeout_field.setText(str(current_timeout))
            self._timeout_field.setPlaceholderText("可选，如 30")
            self._timeout_field.setToolTip("节点级超时（秒），覆盖工作流级默认值")
            self._form.addRow(QLabel("超时（秒）"), self._timeout_field)

        # 重试次数：仅 manifest.retryable=true 时渲染
        replay = getattr(getattr(manifest, "effect", None), "replay", None)
        retryable = getattr(manifest, "retryable", False)
        current_retry = raw_dict.get("retry_count")

        if retryable:
            if not has_own_timeout and self._timeout_field is None:
                # 如果超时字段还没加 header，这里补一个
                header = QLabel("运行设置")
                header.setStyleSheet(f"font-weight: bold; color: {TEXT_HEADING}; padding-top: 8px;")
                self._form.addRow(header)
            self._retry_field = QLineEdit()
            self._retry_field.setValidator(QIntValidator(0, 100))
            if current_retry is not None:
                self._retry_field.setText(str(current_retry))
            self._retry_field.setPlaceholderText("默认 0（不重试）")
            self._retry_field.setToolTip("重试次数（仅声明 retryable 的指令生效）")
            self._form.addRow(QLabel("重试次数"), self._retry_field)
        elif current_retry:
            # 不支持重试但 raw 有遗留值 → 警告 + 清除按钮
            if not has_own_timeout and self._timeout_field is None:
                header = QLabel("运行设置")
                header.setStyleSheet(f"font-weight: bold; color: {TEXT_HEADING}; padding-top: 8px;")
                self._form.addRow(header)
            warn = QLabel(
                "⚠ 该指令不支持重试（"
                + ("重放不安全" if replay == "unsafe" else "未声明")
                + f"），当前值 {current_retry} 不会生效"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {DANGER};")
            clear_btn = QToolButton()
            clear_btn.setText("清除重试次数")
            clear_btn.clicked.connect(lambda: self._clear_retry_count())
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(warn, 1)
            row_layout.addWidget(clear_btn)
            self._form.addRow(row)
            self._retry_field = row

    def _clear_retry_count(self) -> None:
        """清除遗留的 retry_count 值并更新警告文案。"""
        if self._retry_field is not None and isinstance(self._retry_field, QWidget):
            # 找到警告 label 并更新
            for child in self._retry_field.findChildren(QLabel):
                if "⚠" in (child.text() or ""):
                    child.setText("已清除重试次数")
                    child.setStyleSheet(f"color: {SUCCESS};")
                    break

    def retry_timeout_values(self) -> dict[str, Any]:
        """收集超时和重试值，写回 holder.raw 顶层。"""
        result: dict[str, Any] = {}
        if self._timeout_field is not None:
            text = self._timeout_field.text().strip()
            if text:
                result["timeout_seconds"] = float(text)
        if self._retry_field is not None and isinstance(self._retry_field, QLineEdit):
            text = self._retry_field.text().strip()
            if text and text != "0":
                result["retry_count"] = int(text)
        return result

    # ---- 逐类型构建 -------------------------------------------------------
    def _build_field(
        self,
        name: str,
        field_schema: dict[str, Any],
        args: dict[str, Any],
        *,
        target: QFormLayout | None = None,
    ) -> None:
        """按字段类型创建控件并登记；description 进 tooltip。

        表达式控件（fx / Py）的**门控来自 manifest 字段级** ``x-fx`` / ``x-python``
        ——与 Web 编辑器同一份声明、同一套规则。此前桌面端对所有纯 string 字段一律
        给 fx、且不看声明，两端因此长期分化；本轮统一为「读声明」。
        """
        value_type = _effective_type(field_schema)
        has_value = name in args
        row_widget: QWidget | None = None
        allow_fx = bool(field_schema.get("x-fx"))
        allow_python = bool(field_schema.get("x-python"))

        if value_type == "string" and "enum" in field_schema:
            widget = self._build_enum(field_schema, args.get(name), has_value)
            kind = _KIND_ENUM
        elif value_type == "string":
            widget = self._build_text(field_schema, args.get(name), has_value)
            kind = _KIND_TEXT
            if allow_fx or allow_python:
                row_widget = self._wrap_expr_row(
                    name, widget, allow_fx=allow_fx, allow_python=allow_python
                )
        elif value_type == "integer":
            widget = self._build_number_line(
                field_schema, args.get(name), has_value, QIntValidator(-10**9, 10**9),
            )
            kind = _KIND_INT
        elif value_type == "number":
            widget = self._build_number_line(
                field_schema, args.get(name), has_value, QDoubleValidator(-10**9, 10**9, 6),
            )
            kind = _KIND_NUMBER
        elif value_type == "boolean":
            widget = QCheckBox()
            # 未设置（节点里没这个键）时按**声明默认值**起勾，不是一律 False。
            # 此前一律 False，而 manifest 里 `simulateHuman` / `visible` /
            # `ignoreBeforeUnload` 的默认是 `true`：表单显示成「没勾」，用户打开面板
            # 点一次「应用」就把它们写成 `false`——静默翻转行为（点了「模拟人工点击」
            # 的节点被改成不模拟）。默认值的唯一事实源是 manifest，这里只如实呈现。
            widget.setChecked(bool(args.get(name, field_schema.get("default", False))))
            kind = _KIND_BOOL
            if "default" in field_schema:
                self._bool_defaults[name] = bool(field_schema["default"])
        else:
            # array/object 等复合类型：单行 JSON 文本编辑
            text = json.dumps(args[name], ensure_ascii=False) if has_value else ""
            widget = self._build_text(field_schema, text, has_value)
            widget.setPlaceholderText("JSON，如 [\"a\", \"b\"]")
            kind = _KIND_JSON

        self._attach(name, kind, widget, field_schema, row_widget=row_widget, target=target)
        self._attach_element_row(name, widget, target=target)

    @staticmethod
    def _build_enum(
        field_schema: dict[str, Any], current: Any, has_value: bool
    ) -> QComboBox:
        """enum → 下拉框：首项「未设置」(None)，标签优先取 x-enum-labels。"""
        combo = QComboBox()
        combo.addItem("未设置", None)
        labels = field_schema.get("x-enum-labels", {})
        for enum_value in field_schema["enum"]:
            combo.addItem(str(labels.get(enum_value, enum_value)), enum_value)
        if has_value:
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    @staticmethod
    def _build_text(
        field_schema: dict[str, Any], current: Any, has_value: bool
    ) -> QLineEdit:
        """string → 单行输入；空 = 未设置；default 进 placeholder。"""
        edit = QLineEdit()
        if has_value and current is not None:
            edit.setText(str(current))
        default = field_schema.get("default")
        if default is not None:
            edit.setPlaceholderText(f"默认：{default}")
        return edit

    @staticmethod
    def _build_number_line(
        field_schema: dict[str, Any],
        current: Any,
        has_value: bool,
        validator,
    ) -> QLineEdit:
        """integer/number → 带校验器的单行输入（空 = 未设置）。"""
        edit = QLineEdit()
        edit.setValidator(validator)
        if has_value and current is not None:
            edit.setText(str(current))
        minimum = field_schema.get("minimum")
        default = field_schema.get("default")
        hints = []
        if minimum is not None:
            hints.append(f"≥ {minimum}")
        if default is not None:
            hints.append(f"默认 {default}")
        if hints:
            edit.setPlaceholderText("，".join(hints))
        return edit

    def _attach(
        self,
        name: str,
        kind: str,
        widget: QWidget,
        field_schema: dict[str, Any],
        *,
        row_widget: QWidget | None = None,
        target: QFormLayout | None = None,
    ) -> None:
        """登记字段并挂到表单；label 带必填星号，description 进 tooltip。

        row_widget 用于 fx 行容器这类「取值控件 ≠ 行展示控件」的场景：
        登记取值的仍是 widget，挂到表单行的是 row_widget。
        target 为分组区段的 body_layout；None 时回退到主表单 self._form。
        """
        self._fields.append((name, kind, widget))
        # M54 S1：字段标签走 i18n.js 的中文层（命令级覆盖 → 公共键 → 键名原文）；
        # 英文键名降为 tooltip——显示成中文之后，仍要能看见「这个字段在数据里叫什么」。
        display = field_label(name, self._command_id)
        label_text = f"{display} *" if name in self._required else display
        description = field_schema.get("description") or ""
        tip = f"{name}：{description}" if description else name
        if display == name:  # 没有中文标签时键名已在标签上，tooltip 不必再重复
            tip = description
        if tip:
            widget.setToolTip(tip)
        label = QLabel(label_text)
        if description:
            label.setToolTip(description)
        layout = target if target is not None else self._form
        shown = row_widget if row_widget is not None else widget
        layout.addRow(label, shown)
        # M52 S6：元素型参数引用生效时要整行隐藏值控件 ⇒ 把 label 与行内展示控件都留档
        self._row_labels[name] = label
        self._row_shown[name] = shown

    # ---- 表达式控件：fx 变量插入 / Py 表达式（对标影刀与 Web） --------------
    def _wrap_expr_row(
        self,
        name: str,
        editor: QLineEdit,
        *,
        allow_fx: bool,
        allow_python: bool,
    ) -> QWidget:
        """给声明了 ``x-fx`` / ``x-python`` 的文本字段挂「左 Py 右 fx」表达式控件。

        控件拓扑对齐影刀参数框（左 Py 模式图标、右 fx 变量选择器）：

        - **Py**（可选中按钮，仅在声明 ``x-python`` 时出现）：Python 表达式模式；
        - **fx**（普通按钮，仅在声明 ``x-fx`` 时出现）：**不是开关**——点击弹出变量
          列表，选中即把变量插到光标处（不再有「＋变量」这个中间按钮，少一步）。

        两种模式**互斥**，``_expr_modes`` 是唯一真值（运行期据它选 ``resolve_tags``
        还是 ``resolve``）：fx 插入变量 ⇒ 置 ``fx``；Py 选中 ⇒ 置 ``python``。
        变量被删光后 ``fx`` 态自动撤销（见 :meth:`_on_expr_text_changed`），
        杜绝「看着是纯文本、运行期却按标签解析」——字面 CSS 选择器 `a[title]`
        正是这种字段。

        控件必须创建时就带父级（row）：无父级控件一旦 ``setVisible(True)``，Qt 会把它
        当成**顶层窗口**显示——表现为「切换含 fx 的指令时小框闪现」（维护者报障，
        诊断日志实锤：QToolButton 51x23 以 window 身份 Show）。
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        py_button: QToolButton | None = None
        if allow_python:
            py_button = QToolButton(row)
            py_button.setText("Py")
            py_button.setCheckable(True)
            py_button.setToolTip("Python 表达式模式：输入框内容按 Python 表达式求值")
            py_button.toggled.connect(
                lambda checked, k=name: self._on_py_toggled(k, checked)
            )
            layout.addWidget(py_button)

        layout.addWidget(editor, 1)

        fx_button: QToolButton | None = None
        if allow_fx:
            fx_button = QToolButton(row)
            fx_button.setText("fx")
            fx_button.setAutoRaise(True)
            fx_button.setToolTip("选择变量插入：标签模式用 [变量名]，可与文本混排")
            fx_button.clicked.connect(lambda _c=False, k=name: self._show_variable_menu(k))
            layout.addWidget(fx_button)

        self._expr_rows[name] = (py_button, fx_button, editor)
        editor.textChanged.connect(lambda _t, k=name: self._on_expr_text_changed(k))
        self._sync_expr_state(name)
        return row

    def _on_py_toggled(self, name: str, checked: bool) -> None:
        """Py 按钮：选中 = Python 表达式模式；取消 = 回到普通/标签模式。"""
        if checked:
            self._expr_modes[name] = "python"
        elif self._expr_modes.get(name) == "python":
            self._expr_modes.pop(name, None)
        self._sync_expr_state(name)

    def _show_variable_menu(self, name: str) -> None:
        """点击 fx：按需构建并就地弹出变量菜单。

        不预挂 QMenu：预挂菜单在 Windows 上会随控件树重挂/销毁产生原生弹层残影——
        只有表达式字段才有这种控件，正是「切换含 fx 的指令时小框闪现」的来源
        （维护者报障）。按需创建同时保证变量列表每次都是最新。
        """
        row = self._expr_rows.get(name)
        if row is None:
            return
        _py, fx_button, editor = row
        anchor = fx_button or editor
        menu = self._build_variable_menu(name, editor, anchor)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    def _build_variable_menu(
        self, name: str, editor: QLineEdit, parent: QWidget
    ) -> QMenu:
        """构建 fx 变量菜单（点击时调用；也供测试直接驱动）。"""
        menu = QMenu(parent)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        paths = self._variable_provider() if self._variable_provider else []
        if not paths:
            menu.addAction("（无可引用变量）").setEnabled(False)
        for path in paths:
            menu.addAction(path, lambda p=path: self._insert_variable(name, editor, p))
        return menu

    def _insert_variable(self, name: str, editor: QLineEdit, path: str) -> None:
        """在光标处插入变量。

        Python 模式插**变量名原文**（融进表达式，如 ``parameter += 1``）；否则插
        ``[变量名]`` 标签并把字段置为 ``fx`` 态（标签可与文本混排）。
        """
        if self._expr_modes.get(name) == "python":
            editor.insert(path)
            return
        editor.insert(f"[{path}]")
        self._expr_modes[name] = "fx"
        self._sync_expr_state(name)

    def _on_expr_text_changed(self, name: str) -> None:
        """标签被删光 ⇒ 撤销 ``fx`` 态。

        否则字段会停在「文本里没有任何标签、运行期却按标签解析」的状态里——用户
        随后手打的字面 CSS 选择器（``a[title]``）就会命中 ``resolve_tags`` 而报
        ``INVALID_REFERENCE``。
        """
        if self._expr_modes.get(name) != "fx":
            return
        from rpa_core.runtime.resolver import has_tags

        row = self._expr_rows.get(name)
        editor = row[2] if row is not None else None
        if editor is None or has_tags(editor.text()):
            return
        self._expr_modes.pop(name, None)
        self._sync_expr_state(name)

    def _sync_expr_state(self, name: str) -> None:
        """按 ``_expr_modes`` 刷新控件态：Py 选中态 + 编辑器底色提示。"""
        row = self._expr_rows.get(name)
        if row is None:
            return
        py_button, _fx_button, editor = row
        mode = self._expr_modes.get(name)
        if py_button is not None:
            previous = py_button.blockSignals(True)
            py_button.setChecked(mode == "python")
            py_button.blockSignals(previous)
        editor.setStyleSheet(
            f"QLineEdit {{ background: {SURFACE_HIGHLIGHT}; }}" if mode else ""
        )

    # ---- 元素库引用（M52 S4 加入口；M52 S6 去快照 = 唯一真值源） --------------
    def _attach_element_row(
        self, name: str, widget: QWidget, *, target: QFormLayout | None = None
    ) -> None:
        """给支持元素引用的参数（`selector` / `locator`）加一行「元素」。

        出现的条件：调用方给了元素来源，**且**参数键在 runtime 的取值路径表里
        （`element_kind_for_param_key` 以 `_VALUE_BY_KEY` 为准）——能选却替换不了
        = 静默失配，两者必须同一事实源。

        **M52 S6 起引用就是唯一真值**：引用生效时该参数**只显元素名**（`元素：xxx`），
        **值控件整行隐藏**，也不再往 `with` 里写值快照——快照正是「一份参数两份真值」
        的来源，也是「选了元素、selector 却没落进节点」这类事故的土壤。
        ✕ 摘引用 ⇒ 值控件恢复可编辑（内容为之前手填的残留）。
        """
        if self._element_provider is None:
            return
        from rpa_core.runtime.element_refs import (
            element_kind_for_param_key,
            element_value_for_key,
        )

        kind = element_kind_for_param_key(name)
        if kind is None:
            return

        documents: dict[str, dict] = {}
        combo = QComboBox()
        combo.setObjectName(f"elementCombo::{name}")
        combo.setToolTip("从当前流程的元素库选择；运行期按引用取元素库最新值")
        combo.addItem("（不使用元素库）", None)
        for element_name, document in self._element_provider():
            if not isinstance(document, dict) or document.get("kind") != kind:
                continue  # 只列与该参数后端匹配的元素（selector↔browser / locator↔desktop）
            if element_value_for_key(document, name) is None:
                continue  # 取不出定位值的元素，列了也填不进去
            documents[element_name] = document
            combo.addItem(element_name, element_name)

        picked = QLabel("")
        picked.setObjectName(f"elementName::{name}")
        clear = QToolButton()
        clear.setText("✕")
        clear.setObjectName(f"elementClear::{name}")
        clear.setAutoRaise(True)
        clear.setToolTip("摘除元素引用，回到手填")

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(combo, 1)
        layout.addWidget(picked, 1)
        layout.addWidget(clear)

        # 初值：节点已带引用时定位到它。**必须在连信号之前**，否则会被当成用户操作。
        current_ref = (self._raw.get("elementRefs") or {}).get(name)
        if isinstance(current_ref, str):
            index = combo.findData(current_ref)
            if index >= 0:
                combo.setCurrentIndex(index)

        combo.currentIndexChanged.connect(lambda _i, k=name: self._on_element_pick(k))
        clear.clicked.connect(lambda _c=False, k=name: self._on_element_clear(k))

        layout_target = target if target is not None else self._form
        element_label = QLabel("元素库")
        element_label.setObjectName(f"elementLabel::{name}")
        layout_target.addRow(element_label, row)
        self._element_rows[name] = combo
        self._element_picked[name] = picked
        self._element_clears[name] = clear
        self._element_row_labels[name] = element_label
        self._apply_element_state(name)

    def _current_ref(self, name: str) -> str | None:
        """参数当前生效的元素引用：用户显式动过以用户为准，否则取节点里已有的。"""
        if name in self._ref_intent:
            return self._ref_intent[name]
        raw_ref = (self._raw.get("elementRefs") or {}).get(name)
        return raw_ref if isinstance(raw_ref, str) else None

    def _apply_element_state(self, name: str) -> None:
        """两态显示：引用生效 ⇒ **只显元素名**、值控件整行隐藏；否则显示值控件+下拉。"""
        ref = self._current_ref(name)
        active = ref is not None
        label = self._row_labels.get(name)
        if label is not None:
            label.setVisible(not active)
        shown = self._row_shown.get(name)
        if shown is not None:
            shown.setVisible(not active)
        combo = self._element_rows.get(name)
        if combo is not None:
            combo.setVisible(not active)
            if active and combo.findData(ref) >= 0:
                previous = combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(ref))
                combo.blockSignals(previous)
        picked = self._element_picked.get(name)
        if picked is not None:
            # 行标签已经说了这是什么，值里不再重复「元素：」前缀
            picked.setText(str(ref) if active else "")
            picked.setVisible(active)
        element_label = self._element_row_labels.get(name)
        if element_label is not None:
            # 引用态这行就是影刀式「操作目标」（M54 G2：中文口径跟影刀，不跟 Web 的
            # 「选择器」/「定位器」）；非引用态标「元素库」——那时值行已经叫「操作目标」，
            # 两行同名会分不清哪行填什么。
            element_label.setText(
                field_label(name, self._command_id) if active else "元素库"
            )
        clear = self._element_clears.get(name)
        if clear is not None:
            clear.setVisible(active)

    def _on_element_pick(self, name: str) -> None:
        """下拉选择：只记引用意图（None = 摘引用）。

        **不再把元素库的值写进值控件**——那是 M52 S6 之前的快照口径；现在定位值一律
        运行期从元素库取，节点里不留第二份真值。
        """
        combo = self._element_rows.get(name)
        self._ref_intent[name] = combo.currentData() if combo is not None else None
        self._apply_element_state(name)

    def _on_element_clear(self, name: str) -> None:
        """✕ 摘除引用：回到手填（值控件恢复可编辑）。"""
        combo = self._element_rows.get(name)
        if combo is not None and combo.currentIndex() != 0:
            combo.setCurrentIndex(0)  # 触发 _on_element_pick ⇒ 记 None
        else:
            self._ref_intent[name] = None
            self._apply_element_state(name)

    def element_refs(self) -> dict[str, str | None]:
        """用户**显式改过**的元素引用选择（键 → 元素名 / None = 清除引用、回退手填）。

        没动过的键不在结果里——调用方对它们保留节点里已有的引用，否则「打开面板又
        直接应用」会把还在生效的引用误摘（旧的「值被手工改动即摘引用」只剩兜底一支：
        参数字典里真的出现了该键的值 ⇒ 用户改成手填了）。
        """
        return dict(self._ref_intent)

    def expr_modes(self) -> dict[str, str]:
        """各字段当前的表达式模式（``fx``=变量标签 / ``python``=Python 表达式）。

        只回传**当前界面真的有表达式控件**的字段——未声明 ``x-fx``/``x-python`` 的
        字段即使 raw 里残留旧模式也不写回（开发期硬切，不做前向兼容迁移）。
        """
        return {k: v for k, v in self._expr_modes.items() if k in self._expr_rows}

    # ---- 收集 -------------------------------------------------------------
    def values(self) -> dict[str, Any]:
        """把控件当前值收集为参数 dict（保持字段声明顺序）。

        未设置项不进入结果；JSON 字段解析失败时抛 ValueError。

        **被元素引用的参数一律不进结果**（M52 S6）：引用是唯一真值源，节点里不留
        值快照。即便隐藏的值控件里还残留旧文本（例如先手填、后改引用），也不会被
        收进来——否则「一份参数两份真值」又从后门回来了。
        """
        result: dict[str, Any] = {}
        for name, kind, widget in self._fields:
            if self._current_ref(name) is not None:
                continue  # 引用生效 ⇒ 值由运行期从元素库取，节点不存快照
            if kind == _KIND_ENUM:
                chosen = widget.currentData()
                if chosen is None:
                    continue
                result[name] = chosen
            elif kind == _KIND_TEXT:
                text = widget.text()
                if not text:
                    continue
                result[name] = text
            elif kind in (_KIND_INT, _KIND_NUMBER):
                text = widget.text()
                if not text:
                    continue
                result[name] = int(text) if kind == _KIND_INT else float(text)
            elif kind == _KIND_BOOL:
                checked = widget.isChecked()
                # 与**声明默认值**相同的布尔不进结果：运行时「缺省」与「显式等于默认」
                # 等价（执行器一律 `inputs.get(name, <manifest 默认>)`），所以省掉的是
                # 噪声不是语义。两个理由：
                # ① 「元素库插入」与「参数面板选择」两条写入路径要对同一节点**逐字节
                #    相同**（`test_param_form_element_pick_matches_insert_element_output`）；
                # ② 复选框无法表达「未设置」，若一律写入，打开面板再点应用就会给每个
                #    布尔参数塞一个默认值（把「没设」变成「显式设为默认」）。
                # 未声明 default 的布尔（如 `browser.closeTabs.all`）照旧写入——那时
                # 无法判断执行器把缺省当成什么，写了才不会丢用户意图。
                if name in self._bool_defaults and self._bool_defaults[name] is checked:
                    continue
                result[name] = checked
            else:  # JSON 复合字段
                text = widget.text()
                if not text:
                    continue
                try:
                    result[name] = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"字段 {name} 不是合法 JSON：{exc}") from exc
        return result


def build_param_form(
    manifest_or_schema: Any, args: dict[str, Any] | None = None
) -> ParamForm:
    """便捷构造：可传 manifest（取其 input_schema）或直接传 schema dict。"""
    schema = getattr(manifest_or_schema, "input_schema", manifest_or_schema)
    return ParamForm(schema, args)


# ---- 控制流节点表单（if / forEach / try / return） --------------------------
#
# 控制流节点没有 manifest，参数是 AST 结构字段（condition/items/item_var/
# error_var/value）。表单与字段语义对齐 Web 编辑器 renderControlProps：
# - 字面量字段：空串 = ""；"${" 开头 = 引用（保留字符串）；以 [ { 0-9 t f n " -
#   开头尝试 JSON 解析，失败回退为原始字符串（Web literalField 同款）。
# - JSON 字段：空 = 缺省（forEach.items → []，return.value → None），
#   其余必须能通过 JSON 解析（Web jsonField 同款，引用字符串需带引号书写）。
# - 变量名字段（item_var/error_var）：不匹配标识符时拒绝应用并说明原因。

# 与 rpa_core.model.workflow 的标识符约束同口径
NAME_PATTERN = re.compile(r"^[A-Za-z_]\w*$")

CONDITION_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "truthy"]
# 中文标签取自 devserver/static/i18n.js 的 ops 块（全仓唯一一份）。原先这里手抄了一份，
# 加操作符要改两处、且没有门禁钉住两处相等 ⇒ 漂移无症状（M54 G4 收敛）。
CONDITION_OP_LABELS: dict[str, str] = load_op_labels()

_LITERAL_JSON_HEADS = "[{tfn\"-"  # 数字首字符单独用 isdigit() 判定（JS 的 0-9 区间）


def parse_literal(text: str) -> Any:
    """按 Web literalField 语义解析字面量输入。"""
    raw = text.strip()
    if raw == "":
        return ""
    if raw.startswith("${"):
        return raw
    if raw[0].isdigit() or raw[0] in _LITERAL_JSON_HEADS:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def format_literal(value: Any) -> str:
    """字面量字段的显示形式：字符串原样、None 为空、其余 JSON 序列化。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class ControlNodeForm(QWidget):
    """控制流节点参数表单：直接编辑 AST 结构字段，apply_values() 返回字段更新。"""

    def __init__(
        self, node_type: str, raw: dict[str, Any], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.node_type = node_type
        self._form = QFormLayout(self)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._widgets: dict[str, QWidget] = {}

        if node_type == "if":
            condition = dict(raw.get("condition") or {"op": "truthy", "left": ""})
            op_combo = QComboBox()
            for op in CONDITION_OPS:
                op_combo.addItem(CONDITION_OP_LABELS.get(op, op), op)
            current = op_combo.findData(condition.get("op", "truthy"))
            op_combo.setCurrentIndex(current if current >= 0 else 0)
            self._form.addRow(QLabel("条件操作符 *"), op_combo)
            self._widgets["op"] = op_combo

            left = QLineEdit(format_literal(condition.get("left")))
            left.setPlaceholderText("${steps.x.outputs.y} 或字面量")
            self._form.addRow(QLabel("左值 *"), left)
            self._widgets["left"] = left

            right = QLineEdit(format_literal(condition.get("right")))
            right.setPlaceholderText("truthy 时留空")
            right.setToolTip("引用 ${...} 或 JSON 字面量；留空则不设置 right")
            self._form.addRow(QLabel("右值"), right)
            self._widgets["right"] = right

        elif node_type == "forEach":
            items = raw.get("items")
            items_edit = QLineEdit(
                "" if items is None else json.dumps(items, ensure_ascii=False)
            )
            items_edit.setPlaceholderText('["a", "b"] 或带引号的引用 "${rows}"')
            items_edit.setToolTip("JSON 数组；值支持 ${...} 引用（需带引号）")
            self._form.addRow(QLabel("items（数组或引用） *"), items_edit)
            self._widgets["items"] = items_edit

            item_var = QLineEdit(str(raw.get("item_var") or "item"))
            item_var.setToolTip("循环变量名（标识符）")
            self._form.addRow(QLabel("循环变量名 *"), item_var)
            self._widgets["item_var"] = item_var

        elif node_type == "try":
            error_var = QLineEdit(str(raw.get("error_var") or "error"))
            error_var.setToolTip("错误变量名（标识符）")
            self._form.addRow(QLabel("错误变量名 *"), error_var)
            self._widgets["error_var"] = error_var

        elif node_type == "return":
            value = raw.get("value")
            value_edit = QLineEdit(
                "" if value is None else json.dumps(value, ensure_ascii=False)
            )
            value_edit.setPlaceholderText('JSON，如 "ok"；留空返回 null')
            value_edit.setToolTip("JSON 值；引用 ${...} 需带引号书写")
            self._form.addRow(QLabel("返回值"), value_edit)
            self._widgets["value"] = value_edit

            hint = QLabel("return 节点会终止整个工作流并返回该值。")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
            self._form.addRow(hint)

        elif node_type == "sequence":
            hint = QLabel("顺序容器：子节点按顺序执行。")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
            self._form.addRow(hint)

    @staticmethod
    def _name_or_raise(widget: QLineEdit, field: str) -> str:
        """校验标识符字段；非法时抛 ValueError（不静默保留旧值）。"""
        name = widget.text().strip()
        if not NAME_PATTERN.match(name):
            raise ValueError(f"字段 {field} 不是合法标识符：{name or '（空）'}")
        return name

    @staticmethod
    def _json_or_raise(widget: QLineEdit, field: str, empty: Any) -> Any:
        """解析 JSON 字段；空文本返回 empty，非法 JSON 抛 ValueError。"""
        text = widget.text().strip()
        if not text:
            return empty
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"字段 {field} 不是合法 JSON：{exc}") from exc

    def apply_values(self) -> dict[str, Any]:
        """收集为「AST 字段 → 新值」dict；非法输入抛 ValueError。

        返回的键是节点结构字段名（condition/items/item_var/error_var/value），
        调用方负责写回节点 raw dict。sequence 返回空 dict。
        """
        if self.node_type == "if":
            condition: dict[str, Any] = {
                "op": self._widgets["op"].currentData() or "truthy",
                "left": parse_literal(self._widgets["left"].text()),
            }
            right_text = self._widgets["right"].text().strip()
            if right_text:
                condition["right"] = parse_literal(right_text)
            # 右值留空 = 删除 right 键（Web 同语义）
            return {"condition": condition}
        if self.node_type == "forEach":
            return {
                "items": self._json_or_raise(self._widgets["items"], "items", []),
                "item_var": self._name_or_raise(self._widgets["item_var"], "item_var"),
            }
        if self.node_type == "try":
            return {"error_var": self._name_or_raise(self._widgets["error_var"], "error_var")}
        if self.node_type == "return":
            return {"value": self._json_or_raise(self._widgets["value"], "value", None)}
        return {}
