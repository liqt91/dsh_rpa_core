"""指令参数表单（GUI 切片 3）。

把 ``CommandManifest.input_schema``（JSON Schema dict）渲染成原生控件表单，
并支持把编辑结果收回为参数 dict 写回画布卡片。

控件映射（最小切片，fx 表达式切换在后续切片接入）：

- ``string`` + ``enum`` → QComboBox：首项「未设置」，``x-enum-labels``
  提供中文显示文本，itemData 存实际枚举值；
- ``string`` → QLineEdit：空文本 = 未设置，placeholder 提示默认值；
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
        title_label.setStyleSheet("font-weight: bold; color: #4d5564;")
        header_layout.addWidget(title_label, 1)

        count_label = QLabel(str(field_count))
        count_label.setStyleSheet("color: #8b929e;")
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

    fx 变量引用模式（切 C）：纯文本 string 字段带一个 fx 开关；开启后该字段
    以 ``[name]`` 标签引用变量（可与文本混排），并出现「＋变量」下拉在光标处
    插入标签。字段模式经 :meth:`expr_modes` 收回，写回节点 ``_exprModes``。
    运行期由 resolver.resolve_tags 消费，语义与 Web 编辑器一致。
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
    ) -> None:
        super().__init__(parent)
        args = dict(args or {})
        self._required: set[str] = set(schema.get("required", []))
        self._expr_modes: dict[str, str] = dict(expr_modes or {})
        self._variable_provider = variable_provider
        # (字段名, 种类, 控件)：顺序即 properties 声明顺序
        self._fields: list[tuple[str, str, QWidget]] = []
        self._fx_buttons: dict[str, tuple[QToolButton, QToolButton]] = {}
        # 分组模式下的区段引用（section_name → _CollapsibleSection）
        self._sections: dict[str, _CollapsibleSection] = {}
        # 输出别名字段（outField → QLineEdit）
        self._alias_fields: dict[str, QLineEdit] = {}
        self._alias_validator = re.compile(r"^[A-Za-z_]\w*$")

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
        header.setStyleSheet("font-weight: bold; color: #4d5564; padding-top: 8px;")
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
            edit.setStyleSheet("QLineEdit { border: 1px solid #cf222e; }")
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
            header.setStyleSheet("font-weight: bold; color: #4d5564; padding-top: 8px;")
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
                header.setStyleSheet("font-weight: bold; color: #4d5564; padding-top: 8px;")
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
                header.setStyleSheet("font-weight: bold; color: #4d5564; padding-top: 8px;")
                self._form.addRow(header)
            warn = QLabel(
                "⚠ 该指令不支持重试（"
                + ("重放不安全" if replay == "unsafe" else "未声明")
                + f"），当前值 {current_retry} 不会生效"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #cf222e;")
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
                    child.setStyleSheet("color: #1a7f37;")
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
        """按字段类型创建控件并登记；description 进 tooltip。"""
        value_type = _effective_type(field_schema)
        has_value = name in args

        if value_type == "string" and "enum" in field_schema:
            widget = self._build_enum(field_schema, args.get(name), has_value)
            kind = _KIND_ENUM
        elif value_type == "string":
            widget = self._build_text(field_schema, args.get(name), has_value)
            kind = _KIND_TEXT
            row_widget = self._wrap_fx_row(name, widget)
            self._attach(name, kind, widget, field_schema, row_widget=row_widget, target=target)
            return
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
            widget.setChecked(bool(args.get(name, False)))
            kind = _KIND_BOOL
        else:
            # array/object 等复合类型：单行 JSON 文本编辑
            text = json.dumps(args[name], ensure_ascii=False) if has_value else ""
            widget = self._build_text(field_schema, text, has_value)
            widget.setPlaceholderText("JSON，如 [\"a\", \"b\"]")
            kind = _KIND_JSON

        self._attach(name, kind, widget, field_schema, target=target)

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
        label_text = f"{name} *" if name in self._required else name
        description = field_schema.get("description")
        if description:
            widget.setToolTip(description)
        label = QLabel(label_text)
        if description:
            label.setToolTip(description)
        layout = target if target is not None else self._form
        layout.addRow(label, row_widget if row_widget is not None else widget)

    # ---- fx 变量引用模式 ---------------------------------------------------
    def _wrap_fx_row(self, name: str, editor: QLineEdit) -> QWidget:
        """给纯文本字段加 fx 开关与「＋变量」下拉（fx 开启时显示）。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(editor, 1)

        # 必须带父级创建（row）：无父级的控件一旦 setVisible(True)，Qt 会把它
        # 当成**顶层窗口**显示——表现为「切换含 fx 的指令时小框闪现」（维护者
        # 报障，诊断日志实锤：QToolButton 51x23 以 window 身份 Show）。
        var_button = QToolButton(row)
        var_button.setText("＋变量")
        var_button.setToolTip("在光标处插入变量标签 [name]")
        var_button.setAutoRaise(True)

        fx_button = QToolButton(row)
        fx_button.setText("fx")
        fx_button.setCheckable(True)
        fx_button.setChecked(self._expr_modes.get(name) == "fx")
        fx_button.setToolTip(
            "fx 变量引用模式：用 [变量名] 标签引用变量，可与文本混排"
        )

        def show_variable_menu() -> None:
            """点击时按需构建并就地弹出变量菜单。

            不预挂 QMenu：预挂的菜单在 Windows 上会随控件树重挂/销毁产生原生
            弹层残影——只有 fx 字段才有这种控件，正是「切换含 fx 的指令时小框
            闪现」的来源（维护者报障）。按需创建同时保证变量列表每次都是最新。
            """
            menu = self._build_variable_menu(editor, var_button)
            menu.exec(
                var_button.mapToGlobal(var_button.rect().bottomLeft())
            )
            menu.deleteLater()

        def toggle(checked: bool) -> None:
            if checked:
                self._expr_modes[name] = "fx"
            else:
                self._expr_modes.pop(name, None)
            var_button.setVisible(checked)
            # 视觉提示：fx 模式浅蓝底，与 Web 编辑器的 chip 态对应
            editor.setStyleSheet(
                "QLineEdit { background: #eef5ff; }" if checked else ""
            )

        var_button.setVisible(fx_button.isChecked())
        if fx_button.isChecked():
            editor.setStyleSheet("QLineEdit { background: #eef5ff; }")
        fx_button.toggled.connect(toggle)
        var_button.clicked.connect(show_variable_menu)

        layout.addWidget(var_button)
        layout.addWidget(fx_button)
        # 可见性初始化放在加入布局之后（此时必有父级），杜绝顶层窗口闪现
        var_button.setVisible(fx_button.isChecked())
        if fx_button.isChecked():
            editor.setStyleSheet("QLineEdit { background: #eef5ff; }")
        self._fx_buttons[name] = (fx_button, var_button)
        return row

    def _build_variable_menu(self, editor: QLineEdit, parent: QWidget) -> QMenu:
        """构建 fx「＋变量」菜单（点击时调用；也供测试直接驱动）。"""
        menu = QMenu(parent)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        paths = self._variable_provider() if self._variable_provider else []
        if not paths:
            menu.addAction("（无可引用变量）").setEnabled(False)
        for path in paths:
            menu.addAction(path, lambda p=path: editor.insert(f"[{p}]"))
        return menu

    def expr_modes(self) -> dict[str, str]:
        """返回各字段当前的表达式模式（仅 fx 开启的字段）。"""
        return dict(self._expr_modes)

    # ---- 收集 -------------------------------------------------------------
    def values(self) -> dict[str, Any]:
        """把控件当前值收集为参数 dict（保持字段声明顺序）。

        未设置项不进入结果；JSON 字段解析失败时抛 ValueError。
        """
        result: dict[str, Any] = {}
        for name, kind, widget in self._fields:
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
                result[name] = widget.isChecked()
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
# 中文标签与 devserver/static/i18n.js 的 ops 表一致
CONDITION_OP_LABELS = {
    "eq": "等于",
    "ne": "不等于",
    "gt": "大于",
    "gte": "大于等于",
    "lt": "小于",
    "lte": "小于等于",
    "contains": "包含",
    "truthy": "为真（非空）",
}

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
            hint.setStyleSheet("color: #64707d;")
            self._form.addRow(hint)

        elif node_type == "sequence":
            hint = QLabel("顺序容器：子节点按顺序执行。")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #64707d;")
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
