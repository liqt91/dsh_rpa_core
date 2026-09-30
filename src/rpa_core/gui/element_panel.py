"""元素库面板（GUI 功能补齐 切 G）。

与 Web 编辑器元素库同契约：元素是命名流程的附属资产
（``<flow>/elements/<name>.json``，ElementDescriptor 文档）。
面板只做展示与动作入口，存储/校验/捕获逻辑由 app 接线。

捕获后的确认对话框（``ElementDialog``）对齐 Web ``openElementDialog``，自 M44 起是
**捕获即编辑**（对齐影刀「元素编辑器」那一屏）：改名 / 就地编辑定位（browser 主 css 与
候选提升、desktop 勾 locator 字段）/ metadata 只读 / 捕获时命中数展示；同名覆盖保护由
调用方（app）确认。编辑区复用 ``ElementEditorForm``，因此与元素库编辑器**同一份实现、
同一套模型判据**——不是两处口径。

此外把捕获侧已经收集、此前**只入库不展示**的数据也读出来：``selector.candidates``
（备选定位 + 捕获时命中数，运行期自愈按序回退，现已可一键提升为主定位）与 browser 的
语义特征（``role`` / ``accessibleName`` / ``label`` / ``containerText`` / 页面指纹，
仍为只读展示——它们是「为什么这样定位」的依据，不是可编辑的定位本身）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
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

from rpa_core.gui.element_editor import ElementEditorForm
from rpa_core.gui.theme import (
    DANGER,
    SUCCESS,
    TEXT_SECONDARY,
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
        self.hint_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.hint_label)

        # 搜索/过滤（A4，对齐影刀元素库面板）：按名称或摘要（含定位串）过滤列表。
        # 过滤只影响**显示**，不动元素资产本身。
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索元素（名称 / 定位摘要）…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _text: self._render())
        layout.addWidget(self.search_edit)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self._entries: list[dict[str, Any]] = []

    def set_elements(self, entries: list[dict[str, Any]]) -> None:
        """刷新列表；entries 为 {name, summary} dict。过滤框保持原值。"""
        self._entries = list(entries)
        self._render()

    def _render(self) -> None:
        text = self.search_edit.text().strip().lower()
        self.list.clear()
        for entry in self._entries:
            line = f"{entry['name']}    {entry.get('summary', '')}"
            if text and text not in line.lower():
                continue
            self.list.addItem(line)

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


class _VerifyDone(QObject):
    """校验结果跨线程回传：工作线程 emit，对话框线程收（Qt 队列连接保证）。"""

    done = Signal(dict)


class ElementDialog(QDialog):
    """捕获确认对话框 —— **捕获即编辑**（对齐影刀「元素编辑器」那一屏）。

    - 名称可改（默认名由调用方给；同名覆盖保护在 app 侧确认）；
    - **编辑区复用 `ElementEditorForm`**：browser 可就地改主 css / 点一下把某个候选
      设为主定位；desktop 直接勾 locator 字段（不再让用户对着
      `{"backend": "win32", "controlId": 3}` 想办法）。就地结构校验来自同一套模型
      判据，与元素库编辑器**行为一致**；
    - metadata 只读展示（tag/id/classes/text/rect，desktop 另含
      controlType/automationId/window，browser 另含语义特征与页面指纹）；
    - 捕获时命中数：1 绿、其他红（对齐 Web dlg-verify ok/bad）；
    - **三个出口**（影刀同款）：「保存」/「保存并继续」/「重新捕获」，外加取消。
      前两个都过同一套校验（都落盘），「重新捕获」不校验（本次丢弃）；
      调用方用 `intent()` 区分，取消则看 `exec()` 的返回值。

    写回（``result_document``）由编辑区产出、以原 selector 为基底覆盖被编辑的键，
    界面上不暴露的键一律原样保留。
    """

    def __init__(
        self,
        descriptor: dict[str, Any],
        *,
        default_name: str,
        verify_css: Callable[[str], dict] | None = None,
        preview_css: Callable[[str], dict] | None = None,
        clear_preview_css: Callable[[], dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        # 出口意图。默认 "save"：用户直接按「保存」/回车/双击标题栏关闭都走它。
        self._intent = "save"
        self.setWindowTitle("捕获确认")
        self.setMinimumWidth(480)
        self.resize(560, 640)
        layout = QVBoxLayout(self)

        # 捕获时命中数（1=绿 ok，其他=红 bad，对齐 Web dlg-verify）
        verify = descriptor.get("verifyCount")
        self.verify_label = QLabel(
            "" if verify is None else f"捕获时命中 {verify} 个"
        )
        if verify is not None:
            color = SUCCESS if verify == 1 else DANGER
            self.verify_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        layout.addWidget(self.verify_label)

        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("元素名", self.name_edit)
        layout.addLayout(form)

        # 编辑区：与元素库编辑器**同一份**实现（可就地改主 css / 提升候选 / 勾 locator
        # 字段），就地结构校验也来自同一套模型判据。此前这里是一个 selector 文本框——
        # 桌面元素就是一行 locator JSON，用户要勾字段得先保存、再去元素库点「编辑」。
        self.form = ElementEditorForm(descriptor, parent=self)
        layout.addWidget(self.form)
        # 编辑中预览（M48）：挂在**编辑区**（与元素库编辑器同一份实现），确认框只做
        # 接线；关窗（含「重新捕获」）一律清场——预览框不能留在页面上陪用户捕获。
        if descriptor.get("kind") == "browser" and preview_css is not None:
            self.form.enable_live_preview(preview_css, clear_preview_css)
            self.finished.connect(self.form.shutdown_preview)

        # metadata 只读（对齐 Web metaEl 的行集；browser 另含语义特征与页面指纹）
        self.meta_label = QLabel(self._metadata_text(descriptor))
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.meta_label)

        # 备选定位不再是只读标签：它归编辑区管（`ElementEditorForm` 的候选列表，
        # 点一下即设为主定位）。只读展示与可编辑列表摆在同一屏是重复信息，
        # 而「只读」正是确认框此前做不到「捕获即编辑」的一部分。
        # （`candidates_text` 保留：它是 Web 侧 `elementCandidateLines` 的对等物。）

        # 校验错误提示（对话框内联展示，不弹 QMessageBox，保持可测试性）
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {DANGER};")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        # 留引用：`buttonRole()` 在 PySide6 里是**实例方法**（C++ 侧是 static），
        # 想核对「这两个出口是不是 ActionRole」就必须拿得到按钮盒本身。
        self.button_box = buttons
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # 影刀那一屏的另两个出口（M44 S4）。用 ActionRole 是**故意的**：
        # QDialogButtonBox 只把 AcceptRole/RejectRole 自动接到 accepted/rejected，
        # 一旦自动连接，「重新捕获」就会被当成「确定」而去校验元素名与定位——
        # 而它本来就要丢弃本次结果，校验毫无意义且会把用户困在对话框里。
        # 所以两个出口各自决定关窗方式（见 `_close_with`）。
        self.continue_button = buttons.addButton(
            "保存并继续", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.continue_button.setToolTip(
            "保存当前元素，并立即开始捕获下一个（连续采集时不用每次都回元素库）"
        )
        self.continue_button.clicked.connect(self._accept_and_continue)
        self.recapture_button = buttons.addButton(
            "重新捕获", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.recapture_button.setToolTip(
            "丢弃本次捕获结果，回到捕获状态重新框选（不必关窗再点「捕获元素」）"
        )
        self.recapture_button.clicked.connect(self._request_recapture)
        # 活体校验（M47）：browser 元素才有意义（桌面腿的活体查找是另一条通道，
        # 未实现前不摆一个永远转圈的按钮）。回调由调用方注入（app 侧绑定校验通道），
        # 注入 None = 无按钮——offscreen 测试与桌面元素都走这个形状。
        self._verify_css = verify_css
        self._verify_signal = _VerifyDone()
        self._verify_signal.done.connect(self._on_verify_done)
        self.verify_button: QPushButton | None = None
        if verify_css is not None:
            self.verify_button = buttons.addButton(
                "校验元素", QDialogButtonBox.ButtonRole.ActionRole
            )
            self.verify_button.setToolTip(
                "在当前活动标签页现场查找主选择器并黄框闪烁，回显最新命中数"
                "（不是捕获时的旧值）"
            )
            self.verify_button.clicked.connect(self._run_verify)
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

    def _validate_or_report(self) -> bool:
        """保存前校验：元素名非空 + 编辑区无结构错误。通过返回 True。

        「selector 是否合法」**不在这里判**：它由 `ElementEditorForm` 拿模型判据判
        （`css_problems` / `locator_problems`）并就地展示。确认框自己再判一遍等于立
        第二套规则——两套规则必然漂移，而漂移的表现是「确认框放行的东西，执行器读不了」。
        （此前这里手写 `json.loads`，正是那第二套规则：它只验「是不是合法 JSON 对象」，
        验不出「uia locator 一个身份字段都没有」。）
        """
        if not self.name_edit.text().strip():
            self.error_label.setText("元素名不能为空")
            return False
        self.form.revalidate()
        if self.form.blocked:
            return False  # 错误已就地展示在编辑区，保持对话框打开让用户改
        return True

    def _close_with(self, intent: str) -> None:
        """按意图关窗（``super().accept()``：不再走校验，校验由调用方先行）。

        意图与关窗**分开**是有必要的：QDialogButtonBox 的 accepted 信号不带来源，
        三个出口若都靠 `accept()` 收尾，调用方就分不清用户按的是哪个。
        """
        self._intent = intent
        super().accept()

    def accept(self) -> None:
        """「保存」出口：校验通过才关窗。"""
        if self._validate_or_report():
            self._close_with("save")

    def _accept_and_continue(self) -> None:
        """「保存并继续」出口：同样要过校验（它**真的会落盘**），再回捕获态。"""
        if self._validate_or_report():
            self._close_with("save_and_continue")

    def _request_recapture(self) -> None:
        """「重新捕获」出口：丢弃本次结果，**不校验**——本次不落盘，校验没有对象。"""
        self._close_with("recapture")

    # ---- 活体校验（M47 S1）--------------------------------------------------

    def _run_verify(self) -> None:
        """按下「校验元素」：取编辑区**当前** css，经注入的回调活体查找。

        网络往返在工作线程（对话框是模态 exec，阻塞主线程 = 界面冻结）；
        结果经 Qt 信号回对话框线程更新标签。
        """
        if self._verify_css is None or self.verify_button is None:
            return
        css = self.form.css_edit.text().strip()
        if not css:
            self.error_label.setText("先填写主选择器再校验")
            return
        self.error_label.setText("")
        self.verify_button.setEnabled(False)
        self.verify_button.setText("校验中…")

        def work() -> None:
            try:
                result = dict(self._verify_css(css))
            except Exception as exc:  # noqa: BLE001 - 通道故障也要落到标签上
                result = {"error": f"校验通道异常：{exc}"}
            try:
                self._verify_signal.done.emit(result)
            except RuntimeError:
                # 对话框在校验期间被关掉：无处展示，静默结束（daemon 线程自灭）
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_verify_done(self, result: dict) -> None:
        """校验结果回显：命中数换掉「捕获时命中」的旧值；错误就地展示。"""
        if self.verify_button is not None:
            self.verify_button.setEnabled(True)
            self.verify_button.setText("校验元素")
        if result.get("error"):
            self.error_label.setText(f"校验失败：{result['error']}")
            return
        count = result.get("count")
        color = SUCCESS if count == 1 else DANGER
        self.verify_label.setText(f"当前命中 {count} 个（页面上已黄框闪烁）")
        self.verify_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def intent(self) -> str:
        """用户按的是哪个出口：``save`` / ``save_and_continue`` / ``recapture``。

        取消与关闭窗口**不在这里**（``exec()`` 返回 Rejected），调用方先看 exec 结果，
        再看本方法，顺序不能反：Rejected 时 `_intent` 还是默认的 `save`。
        """
        return self._intent

    def result_document(self) -> tuple[str, dict[str, Any]]:
        """编辑结果：（元素名, ElementDescriptor 形状 dict）。仅在 Accepted 后调用。

        文档由编辑区产出（`ElementEditorForm.result_document`）——它以原 selector
        为基底覆盖被编辑的键，界面上不暴露的键（如捕获时收集的 candidates）原样保留。
        """
        return self.name_edit.text().strip(), self.form.result_document()


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
