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
    QWidget,
)

# 字段条目类型标记（决定 values() 如何从控件取值）
_KIND_ENUM = "enum"
_KIND_TEXT = "text"
_KIND_INT = "integer"
_KIND_NUMBER = "number"
_KIND_BOOL = "boolean"
_KIND_JSON = "json"


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
    ) -> None:
        super().__init__(parent)
        args = dict(args or {})
        self._required: set[str] = set(schema.get("required", []))
        self._expr_modes: dict[str, str] = dict(expr_modes or {})
        # 可调用：返回可引用的变量/路径列表（fx 下拉内容）
        self._variable_provider = variable_provider
        # (字段名, 种类, 控件)：顺序即 properties 声明顺序
        self._fields: list[tuple[str, str, QWidget]] = []
        # fx 行控件（字段名 → (fx 开关, ＋变量按钮)），供测试与外部检查
        self._fx_buttons: dict[str, tuple[QToolButton, QToolButton]] = {}

        self._form = QFormLayout(self)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignTop)

        properties = schema.get("properties", {})
        for name, field_schema in properties.items():
            self._build_field(name, field_schema, args)

    # ---- 逐类型构建 -------------------------------------------------------
    def _build_field(
        self, name: str, field_schema: dict[str, Any], args: dict[str, Any]
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
            self._attach(name, kind, widget, field_schema, row_widget=row_widget)
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

        self._attach(name, kind, widget, field_schema)

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
    ) -> None:
        """登记字段并挂到表单；label 带必填星号，description 进 tooltip。

        row_widget 用于 fx 行容器这类「取值控件 ≠ 行展示控件」的场景：
        登记取值的仍是 widget，挂到表单行的是 row_widget。
        """
        self._fields.append((name, kind, widget))
        label_text = f"{name} *" if name in self._required else name
        description = field_schema.get("description")
        if description:
            widget.setToolTip(description)
        label = QLabel(label_text)
        if description:
            label.setToolTip(description)
        self._form.addRow(label, row_widget if row_widget is not None else widget)

    # ---- fx 变量引用模式 ---------------------------------------------------
    def _wrap_fx_row(self, name: str, editor: QLineEdit) -> QWidget:
        """给纯文本字段加 fx 开关与「＋变量」下拉（fx 开启时显示）。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(editor, 1)

        var_button = QToolButton()
        var_button.setText("＋变量")
        var_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        var_button.setToolTip("在光标处插入变量标签 [name]")

        fx_button = QToolButton()
        fx_button.setText("fx")
        fx_button.setCheckable(True)
        fx_button.setChecked(self._expr_modes.get(name) == "fx")
        fx_button.setToolTip(
            "fx 变量引用模式：用 [变量名] 标签引用变量，可与文本混排"
        )

        def refresh_variable_menu() -> None:
            menu = var_button.menu()
            menu.clear()
            paths = (
                self._variable_provider() if self._variable_provider else []
            )
            if not paths:
                menu.addAction("（无可引用变量）").setEnabled(False)
            for path in paths:
                menu.addAction(path, lambda p=path: editor.insert(f"[{p}]"))

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
        # 每次弹出前重建菜单，保证新增变量即时可见
        var_button_menu = QMenu(var_button)
        var_button.setMenu(var_button_menu)
        var_button_menu.aboutToShow.connect(refresh_variable_menu)

        layout.addWidget(var_button)
        layout.addWidget(fx_button)
        self._fx_buttons[name] = (fx_button, var_button)
        return row

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
