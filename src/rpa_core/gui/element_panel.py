"""元素库面板（GUI 功能补齐 切 G）。

与 Web 编辑器元素库同契约：元素是命名流程的附属资产
（``<flow>/elements/<name>.json``，ElementDescriptor 文档）。
面板只做展示与动作入口，存储/校验/捕获逻辑由 app 接线。

捕获后的确认对话框（``ElementDialog``）对齐 Web ``openElementDialog``：
改名 / selector 编辑 / metadata 只读 / 捕获时命中数展示；同名覆盖保护由
调用方（app）确认。此外把捕获侧已经收集、此前**只入库不展示**的两类数据也读出来：
``selector.candidates``（备选定位 + 捕获时命中数，运行期自愈按序回退）与 browser 的
语义特征（``role`` / ``accessibleName`` / ``label`` / ``containerText`` / 页面指纹）。
两者都是只读展示——编辑它们的能力属于元素编辑器，不属于确认框。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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


class ElementPanel(QWidget):
    """元素库面板：列表 + 刷新/捕获/编辑/结构校验/删除/插入按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)

        toolbar = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.capture_button = QPushButton("捕获元素")
        # 手势文案按平台取：macOS 上 Ctrl+Click 会被系统改写成右键（见 capture_click_label）
        from rpa_core.capture import capture_click_label

        self.capture_button.setToolTip(
            "混合捕获：移动鼠标框选（网页走浏览器插件、桌面走 UIA），"
            f"{capture_click_label()} 或右键捕获（桌面也可用 F9），Esc 取消"
        )
        # 按钮文案与提示都点明「结构」：这个动作**不连接页面/窗口**，只校验元素文档与
        # selector 是否合法。对齐 Web 侧（按钮 title="结构校验"，devserver 返回
        # note「结构校验；活体验证（命中数）需在捕获会话内完成」）。
        # 影刀那侧是**活体**校验（点一下页面真的高亮闪烁），迁来的用户最易把这里的
        # 「通过」读成「页面上定位得到」——静默误解正是本项目最要避免的一类错误。
        self.verify_button = QPushButton("结构校验")
        self.verify_button.setToolTip(
            "结构校验：只检查元素文档与 selector 是否合法，不连接页面或窗口。"
            "活体验证（实际命中数）在捕获时完成，结果显示在捕获确认框里。"
        )
        self.edit_button = QPushButton("编辑")
        self.edit_button.setToolTip(
            "打开元素编辑器：浏览器元素可把捕获到的备选定位一键设为主定位；"
            "桌面元素用勾选框改 locator 字段（不用手写 JSON）。保存前就地结构校验。"
        )
        self.insert_button = QPushButton("插入参数")
        self.insert_button.setToolTip(
            "把选中元素填入画布当前指令的 selector/locator 参数"
        )
        self.delete_button = QPushButton("删除")
        for button in (
            self.refresh_button,
            self.capture_button,
            self.edit_button,
            self.verify_button,
            self.insert_button,
            self.delete_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.hint_label)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

    def set_elements(self, entries: list[dict[str, Any]]) -> None:
        """刷新列表；entries 为 {name, summary} dict。"""
        self.list.clear()
        for entry in entries:
            self.list.addItem(f"{entry['name']}    {entry.get('summary', '')}")

    def current_name(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.text().split(" ", 1)[0].strip()


def _as_dict(value: Any) -> dict[str, Any]:
    """元素文档里的 selector/locator 是自由 JSON；取用前统一收口。

    元素文件可能被手工改成任意形状（``rpa-core elements verify`` 的职责正是报告这类
    问题），GUI 不应因此崩在取字段这一步。
    """
    return value if isinstance(value, dict) else {}


def summarize_element(document: dict) -> str:
    """元素摘要：kind · selector 简述（对齐 Web elementSummary）。"""
    kind = document.get("kind", "?")
    selector = _as_dict(document.get("selector"))
    if kind == "browser":
        return f"browser · {selector.get('css', '')}"
    locator = _as_dict(selector.get("locator"))
    name = locator.get("name") or locator.get("controlType") or ""
    return f"desktop · {name}"


# 元数据里可能很长的值（页面 URL / 容器文本上限 200 字）截断展示，避免撑爆对话框。
_META_DISPLAY_LIMIT = 80


def _clip(value: Any) -> str:
    """压平空白并截断到展示上限（末尾带省略号）。"""
    text = " ".join(str(value).split())
    if len(text) <= _META_DISPLAY_LIMIT:
        return text
    return text[: _META_DISPLAY_LIMIT] + "…"


def semantic_meta_text(descriptor: dict[str, Any]) -> str:
    """browser 元素的语义特征与页面指纹（desktop 没有这些键，返回空串）。

    这些字段是捕获侧为「元素改版后按候选排序 / 命中多个时人工消歧」收集的（见
    ``ElementDescriptor`` 文档），但此前 GUI 只展示 tag/id/classes/text/rect ——
    **保住数据做到了、暴露数据没做**：用户既不知道这些信息存在，也无从据它判断
    「这个元素为什么会被这样定位」。``accessibleName`` / ``label`` 恰恰是消歧最有用
    的两项。纯展示，不改变任何写回逻辑。
    """
    if descriptor.get("kind") != "browser":
        return ""
    meta = descriptor.get("metadata") or {}
    lines = [
        meta.get("role") and f"role: {meta['role']}",
        meta.get("accessibleName") and f"accessibleName: {_clip(meta['accessibleName'])}",
        meta.get("placeholder") and f"placeholder: {_clip(meta['placeholder'])}",
        meta.get("label") and f"label: {_clip(meta['label'])}",
        meta.get("containerText") and f"containerText: {_clip(meta['containerText'])}",
        meta.get("url") and f"url: {_clip(meta['url'])}",
        meta.get("title") and f"title: {_clip(meta['title'])}",
    ]
    return "\n".join(line for line in lines if line)


def candidates_text(descriptor: dict[str, Any]) -> str:
    """备选定位展示文本（无候选返回空串）。

    候选由捕获侧按 ``{kind, selector, matchedCount}`` 收集（browser 专有，见
    ``_candidate_errors``），运行期主选择器失效时按序回退。``matchedCount > 1`` 的候选
    本身不唯一 —— 回退到它有可能点到别的元素，这里如实标出「不唯一」而不是替用户
    过滤掉：选择更稳的候选是用户的判断，不是展示层的判断。
    """
    raw = _as_dict(descriptor.get("selector")).get("candidates")
    if not isinstance(raw, list):
        return ""
    items = [item for item in raw if isinstance(item, dict)]
    if not items:
        return ""
    lines = [f"备选定位 {len(items)} 条（主选择器失效时按序回退）"]
    for index, item in enumerate(items, start=1):
        matched = item.get("matchedCount")
        if isinstance(matched, int) and not isinstance(matched, bool):
            match_text = f"命中 {matched}" + ("（不唯一）" if matched > 1 else "")
        else:
            match_text = "命中未实测"
        kind = item.get("kind") or "?"
        lines.append(f"  {index}. [{kind}] {item.get('selector') or ''} · {match_text}")
    return "\n".join(lines)


class ElementDialog(QDialog):
    """捕获确认对话框（对齐 Web ``openElementDialog`` 的 confirm 模式）。

    - 名称可改（默认名由调用方给；同名覆盖保护在 app 侧确认）；
    - selector 可编辑：browser 为 css 单行，desktop 为 locator JSON；
    - metadata 只读展示（tag/id/classes/text/rect，desktop 另含
      controlType/automationId/window，browser 另含语义特征与页面指纹）；
    - 备选定位只读展示（``selector.candidates`` + 捕获时命中数，见
      ``candidates_text``）；
    - 捕获时命中数：1 绿、其他红（对齐 Web dlg-verify ok/bad）。

    只有前三行里的「名称 / selector」是可写的；其余全是**展示**。写回
    （``result_document``）以原 selector 为基底覆盖单个键，界面不展示的键一律原样保留。
    """

    def __init__(
        self,
        descriptor: dict[str, Any],
        *,
        default_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        self.setWindowTitle("捕获确认")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        # 捕获时命中数（1=绿 ok，其他=红 bad，对齐 Web dlg-verify）
        verify = descriptor.get("verifyCount")
        self.verify_label = QLabel(
            "" if verify is None else f"捕获时命中 {verify} 个"
        )
        if verify is not None:
            color = "#1a7f37" if verify == 1 else "#cf222e"
            self.verify_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        layout.addWidget(self.verify_label)

        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("元素名", self.name_edit)
        selector = _as_dict(descriptor.get("selector"))
        if descriptor.get("kind") == "browser":
            selector_text = str(selector.get("css") or "")
        else:
            selector_text = json.dumps(
                _as_dict(selector.get("locator")), ensure_ascii=False
            )
        self.selector_edit = QLineEdit(selector_text)
        form.addRow("selector", self.selector_edit)
        layout.addLayout(form)

        # metadata 只读（对齐 Web metaEl 的行集；browser 另含语义特征与页面指纹）
        self.meta_label = QLabel(self._metadata_text(descriptor))
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet("color: #64707d;")
        layout.addWidget(self.meta_label)

        # 备选定位（只读）：捕获侧收集、运行期自愈按序回退。此前完全不展示，
        # 用户既不知道自愈能力存在，也无法在命中多个时挑一个更稳的候选。
        # 只读且可选中复制 —— 不做「点选设为主动选择器」，那是元素编辑器的职责。
        self.candidates_label = QLabel(candidates_text(descriptor))
        self.candidates_label.setWordWrap(True)
        self.candidates_label.setStyleSheet("color: #64707d; font-family: monospace;")
        self.candidates_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.candidates_label.setToolTip(
            "捕获时额外收集的备选定位，运行期主选择器失效时按序回退（自动自愈）。"
            "「不唯一」表示该候选本身命中多个元素。"
        )
        if not self.candidates_label.text():
            self.candidates_label.hide()
        layout.addWidget(self.candidates_label)

        # 校验错误提示（对话框内联展示，不弹 QMessageBox，保持可测试性）
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #cf222e;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _metadata_text(descriptor: dict[str, Any]) -> str:
        meta = descriptor.get("metadata") or {}
        rect = meta.get("rect") or {}
        rect_text = ""
        if rect:
            rect_text = (
                f"rect: {rect.get('width')}×{rect.get('height')} "
                f"@ ({rect.get('x')},{rect.get('y')})"
            )
        is_desktop = descriptor.get("kind") == "desktop"
        classes = meta.get("classes") or []
        lines = [
            meta.get("tag") and f"tag: {meta['tag']}",
            meta.get("id") and f"id: {meta['id']}",
            classes and f"classes: {' '.join(str(c) for c in classes)}",
            meta.get("text") and f"text: {meta['text']}",
            rect_text,
            is_desktop and meta.get("controlType") and f"controlType: {meta['controlType']}",
            is_desktop and meta.get("automationId") and f"automationId: {meta['automationId']}",
            is_desktop and meta.get("name") and f"name: {meta['name']}",
            # className 是 win32 侧定位**无窗口文本控件**（ListBox/ComboBox）的唯一手段，
            # 也是 `classNameRe` 正则的输入。捕获 agent 一直带着它回传，GUI 却从没显示过
            # （2026-09-23 探针 `probe_element_dialog_display.py` 实测发现）。
            is_desktop and meta.get("className") and f"className: {_clip(meta['className'])}",
            is_desktop and meta.get("windowTitle") and f"window: {meta['windowTitle']}",
            # browser 的语义特征与页面指纹（见 semantic_meta_text）
            semantic_meta_text(descriptor),
        ]
        # 刻意**不展示** windowHandle / point：它们是本次会话的运行期值（句柄每次启动都变），
        # 摆出来会诱导用户粘进 locator，写出一个下次必定失效的元素。
        return "\n".join(line for line in lines if line) or "(无 metadata)"

    def accept(self) -> None:
        """保存前校验：名称/selector 非空；desktop locator 必须是合法 JSON 对象。"""
        if not self.name_edit.text().strip():
            self.error_label.setText("元素名不能为空")
            return
        if not self.selector_edit.text().strip():
            self.error_label.setText("selector 不能为空")
            return
        if self._descriptor.get("kind") != "browser":
            try:
                locator = json.loads(self.selector_edit.text().strip())
            except json.JSONDecodeError:
                self.error_label.setText("desktop locator 不是合法 JSON")
                return
            if not isinstance(locator, dict):
                self.error_label.setText("desktop locator 必须是 JSON 对象")
                return
        super().accept()

    def result_document(self) -> tuple[str, dict[str, Any]]:
        """编辑结果：（元素名, ElementDescriptor 形状 dict）。仅在 Accepted 后调用。"""
        name = self.name_edit.text().strip()
        # 以原 selector 为基底再覆盖界面暴露的那一个键。**不能从头重建**：
        # selector 里还有界面上不展示的键（捕获时收集的 candidates 备选定位），
        # 重建会让用户编辑一次就把它们静默抹掉。
        raw_selector = _as_dict(self._descriptor.get("selector"))
        selector: dict[str, Any] = dict(raw_selector)
        if self._descriptor.get("kind") == "browser":
            selector["css"] = self.selector_edit.text().strip()
        else:
            selector["locator"] = json.loads(self.selector_edit.text().strip())
        return name, {
            "kind": self._descriptor.get("kind"),
            "selector": selector,
            "verifyCount": self._descriptor.get("verifyCount") or 0,
            "metadata": self._descriptor.get("metadata") or {},
        }


def wire_element_panel(
    panel: ElementPanel,
    *,
    on_refresh: Callable[[], None],
    on_capture: Callable[[], None],
    on_verify: Callable[[str], None],
    on_edit: Callable[[str], None],
    on_insert: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> None:
    """把面板按钮接到 app 侧逻辑（名称参数从当前选中元素取）。"""
    panel.refresh_button.clicked.connect(on_refresh)
    panel.capture_button.clicked.connect(on_capture)

    def with_name(handler: Callable[[str], None]):
        def run() -> None:
            name = panel.current_name()
            if name:
                handler(name)
        return run

    panel.verify_button.clicked.connect(with_name(on_verify))
    panel.edit_button.clicked.connect(with_name(on_edit))
    # 列表双击也进编辑器：影刀的元素库就是双击打开编辑器，用户有这个肌肉记忆。
    # 这里直接连 with_name(on_edit)：双击必然有选中项，走同一条路径就不会出现
    # 「按钮能编辑、双击不能」这种行为分叉。
    panel.list.itemDoubleClicked.connect(lambda _item: with_name(on_edit)())
    panel.insert_button.clicked.connect(with_name(on_insert))
    panel.delete_button.clicked.connect(with_name(on_delete))
