"""工作台「指令测试」页签（M38 S1.2）：启动 L1 契约矩阵并展示结果。

## 为什么是这个形态

维护者要「一个页面做启动和展示」。ADR 0016 定 GUI 为唯一主力形态、devserver 冻结演进，所以
它落在工作台（`HomeWindow` 的第三个页签：流程库 / 运行历史 / 指令测试），而不是再起一个本地
Web 页——那会引入第二个 UI 面，还要额外过架构门禁的隔离断言。

## 页面只做「点火 + 展示」，测试逻辑一行都不在这儿

- **覆盖范围来自用例表**（`tests/commands/cases/*.json`，唯一事实来源）。页面按**命名空间**
  读它，所以 M38 S2（桌面 36 条）/ S3（数据 + 工作流 18 条）建表后**自动出现**，不用改本文件。
  未建表的命名空间也列出来，避免「怎么只有浏览器」的困惑。
- **结果来自被测方自己的结构化报告**（`RPA_COMMAND_MATRIX_REPORT` 指向的 JSONL，由
  `tests/commands/conftest.py` §2 逐用例 append + flush），页面**按字节偏移增量读**。
  不解析 pytest 输出：`pyproject.toml` 的 `addopts = "-q"` 会把 `-v` 抵消掉，靠 verbosity
  实测算不出来任何逐用例信息。
- **跑测试是另起子进程**（`python -m pytest tests/commands`）：GUI 不承载 runtime（ADR 0011），
  也不该把 pytest 搬进 GUI 进程——pytest 会改全局状态（断言改写、warnings 过滤等）。

## 安全边界

矩阵的用例层已有两道守卫（`tests/commands/conftest.py` §1 钉死终止进程 / 拉起浏览器、
`tests/conftest.py` 钉死全局输入面），所以这个按钮点下去不会动维护者的浏览器与键盘。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 与 tests/commands/conftest.py 的开关/报告环境变量同名——两边是一份约定，改一处要改两处
MATRIX_ENV = "RPA_COMMAND_MATRIX"
REPORT_ENV = "RPA_COMMAND_MATRIX_REPORT"
PYTEST_TARGET = "tests/commands"
CASES_DIR = Path("tests") / "commands" / "cases"
STATIC_CHECK = Path(".harness") / "scripts" / "check_command_matrix.py"

_OUTCOME_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "error": "错误",
    "skipped": "跳过",
}
_OUTCOME_COLORS = {
    "passed": "#1a7f37",
    "failed": "#cf222e",
    "error": "#cf222e",
    "skipped": "#9a6700",
}
_NEUTRAL = "#57606a"
# 日志面板留最后多少行（矩阵一跑就是几百行，全留着没意义还占内存）
_LOG_MAX_LINES = 2000


# ---- 纯函数（可独立测试，不依赖 Qt） -------------------------------------


def find_repo_root(start: Path | None = None) -> Path | None:
    """定位仓库根（用例表所在处）。找不到返回 `None`——安装态下 `tests/` 不随包发布。

    优先 `start`，其次本文件回溯（`src/rpa_core/gui/` → 上溯 3 层），最后当前工作目录。
    """
    candidates: list[Path] = []
    if start is not None:
        candidates.append(start)
    candidates.extend(Path(__file__).resolve().parents[3:4])
    candidates.append(Path.cwd())
    for candidate in candidates:
        if (candidate / CASES_DIR).is_dir():
            return candidate
    return None


@dataclass(frozen=True)
class NamespaceInfo:
    """一个已建表命名空间的覆盖规模。"""

    name: str
    commands: int
    variants: int


def load_case_table(root: Path) -> dict[str, dict]:
    """合并 `cases/*.json`；坏文件跳过（页面是只读消费者，不该被坏数据拦住）。"""
    merged: dict[str, dict] = {}
    cases_dir = root / CASES_DIR
    if not cases_dir.is_dir():
        return merged
    for path in sorted(cases_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def describe_namespaces(root: Path) -> tuple[list[NamespaceInfo], list[str]]:
    """返回 `(已建表命名空间, 未建表命名空间)`。

    「未建表」= `commands/<ns>/` 有命令目录、但 `cases/<ns>.json` 不存在。这与
    `.harness/scripts/check_command_matrix.py` 的 `PENDING_NAMESPACES` 是同一件事的两种视图：
    那边是门禁台账，这边是给人看的提示——页面**不读台账**，免得两处口径各说各话。
    """
    cases_dir = root / CASES_DIR
    built: list[NamespaceInfo] = []
    if cases_dir.is_dir():
        for path in sorted(cases_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            variants = sum(len(spec.get("variants") or []) for spec in data.values())
            built.append(NamespaceInfo(path.stem, len(data), variants))

    pending: list[str] = []
    commands_root = root / "commands"
    if commands_root.is_dir():
        for child in sorted(commands_root.iterdir()):
            if child.is_dir() and not (cases_dir / f"{child.name}.json").exists():
                pending.append(child.name)
    return built, pending


def total_variants(root: Path) -> int:
    """已建表命名空间的变体总数（进度条分母）。"""
    built, _ = describe_namespaces(root)
    return sum(info.variants for info in built)


def build_pytest_command(python: str) -> list[str]:
    """子进程 argv。

    `-p no:cacheprovider`：不写 `.pytest_cache`（这个按钮会被反复点，别污染工作树）。
    `console_output_style=count`：日志面板里是 `[ 69/180]` 这种进度标记，比满屏点号好读。
    `--no-header`：这几行（rootdir/plugins）对使用者没有信息量，删掉让日志更干净。

    **报告路径不在这里**：pytest 没有这样的选项，路径由环境变量 `RPA_COMMAND_MATRIX_REPORT`
    传给 `tests/commands/conftest.py` 的钩子（见 `_run_matrix` 里组装环境的地方）。
    """
    return [
        python,
        "-m",
        "pytest",
        PYTEST_TARGET,
        "-p",
        "no:cacheprovider",
        "--no-header",
        "-o",
        "console_output_style=count",
    ]


def read_new_lines(path: Path, offset: int) -> tuple[list[dict], int]:
    """从 `offset` 处增量读 JSONL，返回 `(记录列表, 新偏移)`。

    最后一行可能是**半行**（子进程刚写了一半）：这时不推进偏移，等下一轮再读——否则会把
    半个 JSON 当坏数据丢掉，那一条结果就永久缺失了。
    """
    if not path.exists():
        return [], offset
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
    except OSError:
        return [], offset
    if not chunk:
        return [], offset

    text = chunk.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        consumed = len(chunk)
    else:
        cut = text.rfind("\n")
        if cut < 0:
            return [], offset  # 整块都是半行
        text = text[: cut + 1]
        consumed = len(text.encode("utf-8"))

    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue  # 坏行跳过：不让一行垃圾拦住后面所有结果
        if isinstance(parsed, dict):
            records.append(parsed)
    return records, offset + consumed


@dataclass
class MatrixState:
    """一次矩阵运行的累计状态（页面与测试都只跟它打交道）。"""

    counts: dict[str, int] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    summary: dict | None = None

    def apply(self, record: dict) -> None:
        event = record.get("event")
        if event == "case":
            outcome = str(record.get("outcome") or "error")
            self.counts[outcome] = self.counts.get(outcome, 0) + 1
            self.records.append(record)
        elif event == "summary":
            self.summary = record
            counts = record.get("counts")
            if isinstance(counts, dict):
                self.counts = {str(key): int(value) for key, value in counts.items()}

    @property
    def done(self) -> int:
        return sum(self.counts.values())

    @property
    def failed(self) -> int:
        return self.counts.get("failed", 0) + self.counts.get("error", 0)

    def failures(self) -> list[dict]:
        return [
            record
            for record in self.records
            if record.get("outcome") in ("failed", "error")
        ]

    def describe(self, total: int | None = None) -> str:
        """一行摘要，如「已跑 12 / 180 · 通过 11 · 失败 1」。"""
        head = f"已跑 {self.done}" + (f" / {total}" if total else "")
        parts = [head]
        for key in ("passed", "failed", "error", "skipped"):
            count = self.counts.get(key, 0)
            if count:
                parts.append(f"{_OUTCOME_LABELS[key]} {count}")
        return " · ".join(parts)


# ---- 页签 ----------------------------------------------------------------


class CommandMatrixPanel(QWidget):
    """工作台「指令测试」页签：启动/停止 + 进度 + 结果树 + 输出日志。"""

    def __init__(self, *, repo_root: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._root = find_repo_root(repo_root)
        self._report_dir = Path(tempfile.gettempdir()) / "rpa_core_command_matrix"
        self._report_path: Path | None = None
        self._offset = 0
        self._state = MatrixState()
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._mode: str | None = None

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._drain_output)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_process_error)

        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll_report)

        self._build_ui()

    # ---- 构建 -------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QLabel("指令测试")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        self.scope_label = QLabel(self._scope_text())
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("运行 L1 契约矩阵")
        self.run_button.setToolTip(
            "另起子进程跑 tests/commands 的全量参数矩阵（假扩展，不碰本机浏览器）"
        )
        self.run_button.clicked.connect(self._on_run_clicked)
        self.static_button = QPushButton("跑覆盖率校验")
        self.static_button.setToolTip(
            "只读用例表与 manifest 的静态校验（毫秒级）——就是默认门禁里的那一步"
        )
        self.static_button.clicked.connect(self._run_static_check)
        self.report_button = QPushButton("打开报告目录")
        self.report_button.setToolTip("结构化结果（JSONL）落盘处")
        self.report_button.clicked.connect(self._open_report_dir)
        for widget in (self.run_button, self.static_button, self.report_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m")
        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {_NEUTRAL};")
        row.addWidget(self.progress, 1)
        row.addWidget(self.status)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.results = QTreeWidget()
        self.results.setColumnCount(4)
        self.results.setHeaderLabels(["命令 / 变体", "状态", "耗时(ms)", "失败原因"])
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setAlternatingRowColors(True)
        self.results.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.results.header().setStretchLastSection(True)
        self.results.setColumnWidth(0, 320)
        self.results.setColumnWidth(1, 70)
        self.results.setColumnWidth(2, 90)
        splitter.addWidget(self.results)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(_LOG_MAX_LINES)
        self.log.setPlaceholderText("运行输出会出现在这里")
        splitter.addWidget(self.log)
        splitter.setSizes([380, 180])
        layout.addWidget(splitter, 1)

    def _scope_text(self) -> str:
        if self._root is None:
            return (
                "找不到用例表目录（tests/commands/cases）——本页需要在源码仓库里运行"
                "（安装态不随包发布用例表）。"
            )
        built, pending = describe_namespaces(self._root)
        if not built:
            return "用例表为空。"
        covered = " / ".join(
            f"{info.name} {info.commands} 条命令 · {info.variants} 个变体" for info in built
        )
        text = f"已覆盖：{covered}。"
        if pending:
            text += f"未建表（不参与本次运行）：{' / '.join(pending)}。"
        return text

    # ---- 交互 -------------------------------------------------------------
    def _on_run_clicked(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._stop()
            return
        self._run_matrix()

    def _run_matrix(self) -> None:
        if self._root is None:
            self._set_status(self._scope_text(), _OUTCOME_COLORS["failed"])
            return
        self._report_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report_path = self._report_dir / f"matrix-{stamp}.jsonl"
        report_path.write_text("", encoding="utf-8", newline="\n")
        self._report_path = report_path
        self._offset = 0
        self._state = MatrixState()
        self._rows.clear()
        self.results.clear()
        self.log.clear()
        self.progress.setRange(0, max(total_variants(self._root), 1))
        self.progress.setValue(0)
        env = QProcessEnvironment.systemEnvironment()
        env.insert(MATRIX_ENV, "1")
        env.insert(REPORT_ENV, str(report_path))
        env.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(self._root))
        self._start(build_pytest_command(sys.executable), "matrix")
        self._timer.start()

    def _run_static_check(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            return
        if self._root is None:
            self._set_status(self._scope_text(), _OUTCOME_COLORS["failed"])
            return
        self.log.clear()
        self._state = MatrixState()
        self._rows.clear()
        self.results.clear()
        self.progress.setRange(0, 0)  # 不确定进度：这一步没有「变体数」可言
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(self._root))
        self._start([sys.executable, str(STATIC_CHECK)], "static")

    def _start(self, argv: list[str], mode: str) -> None:
        self._mode = mode
        # 左侧按钮同时是「启动」与「停止」：运行期间两个按钮都不该再触发第二次运行
        self.run_button.setText("停止")
        self.static_button.setEnabled(False)
        self._set_status("运行中…")
        self._process.start(argv[0], argv[1:])

    def _stop(self) -> None:
        # 强杀而非 terminate：pytest 没有可用的优雅退出信号，且这个按钮的语义就是「现在停」。
        # 报告是逐行 flush 的，所以已经跑完的用例不会丢。
        self._process.kill()

    def _idle_buttons(self) -> None:
        self.run_button.setText("运行 L1 契约矩阵")
        self.run_button.setEnabled(True)
        self.static_button.setEnabled(True)

    def _open_report_dir(self) -> None:
        self._report_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_dir)))

    # ---- 子进程信号 -------------------------------------------------------
    def _drain_output(self) -> None:
        raw = bytes(self._process.readAllStandardOutput())
        if raw:
            self.log.appendPlainText(raw.decode("utf-8", errors="replace").rstrip("\n"))

    def _poll_report(self) -> None:
        if self._report_path is None:
            return
        records, self._offset = read_new_lines(self._report_path, self._offset)
        for record in records:
            self._state.apply(record)
            if record.get("event") == "case":
                self._append_case(record)
        total = self.progress.maximum() if self.progress.maximum() > 0 else None
        self.progress.setValue(min(self._state.done, self.progress.maximum()))
        self._set_status(self._state.describe(total))

    def _append_case(self, record: dict) -> None:
        command = str(record.get("command") or "")
        variant = str(record.get("variant") or record.get("node") or "")
        if not command:
            return
        parent = self._rows.get(command)
        if parent is None:
            parent = QTreeWidgetItem(self.results, [command, "", "", ""])
            self._rows[command] = parent
        outcome = str(record.get("outcome") or "")
        duration = record.get("durationMs")
        item = QTreeWidgetItem(
            parent,
            [
                variant,
                _OUTCOME_LABELS.get(outcome, outcome),
                "" if duration is None else f"{float(duration):.1f}",
                "",
            ],
        )
        color = _OUTCOME_COLORS.get(outcome)
        if color:
            # 注意是 QBrush/QColor 不是颜色字符串（`setForeground` 不收 str——被契约测试抓过）
            brush = QBrush(QColor(color))
            item.setForeground(1, brush)
            if outcome != "passed":
                parent.setForeground(1, brush)

    def _on_finished(self, exit_code: int, _status) -> None:
        self._timer.stop()
        self._poll_report()
        mode = self._mode
        self._mode = None
        self._idle_buttons()

        if mode != "matrix":
            # 静态校验没有「变体」概念，它的结论就在自己那行输出里
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self._set_status(
                self._last_log_line() or f"退出码 {exit_code}",
                _OUTCOME_COLORS["passed"] if exit_code == 0 else _OUTCOME_COLORS["failed"],
            )
            return

        self.progress.setRange(0, max(self.progress.maximum(), self._state.done))
        self.progress.setValue(self._state.done)
        if self._state.summary is None:
            self._set_status(
                "运行结束但没有结果记录——子进程可能在收集/导入阶段就失败了（见下方输出）。"
                "常见原因：pytest 未安装（需开发依赖）。",
                _OUTCOME_COLORS["failed"],
            )
            return
        if exit_code == 0 and self._state.failed == 0:
            self._set_status(f"完成：{self._state.describe()}", _OUTCOME_COLORS["passed"])
        else:
            self._set_status(
                f"有失败：{self._state.describe()}（子进程退出码 {exit_code}）",
                _OUTCOME_COLORS["failed"],
            )

    def _on_process_error(self, error) -> None:
        self._timer.stop()
        self._idle_buttons()
        self._set_status(f"无法启动子进程：{error}", _OUTCOME_COLORS["failed"])

    # ---- 内部 -------------------------------------------------------------
    def _last_log_line(self) -> str:
        for line in reversed(self.log.toPlainText().splitlines()):
            if line.strip():
                return line.strip()
        return ""

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color or _NEUTRAL};")

    def shutdown(self) -> None:
        """关窗口时收掉子进程，别留下一个还在跑矩阵的孤儿。"""
        self._timer.stop()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
