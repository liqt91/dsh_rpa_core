"""M38 S1.2 工作台「指令测试」页签契约。

分三层：
1. 纯函数（不依赖 Qt）：仓库根定位、命名空间描述、argv 组装、JSONL 增量读、状态累计；
2. 数据合同：`tests/commands/conftest.py` 的实时报告钩子**真跑一次**能产出预期 JSONL
   （只跑一个变体，`-k` 收窄，毫秒级）；
3. 页签装配：工作台第三个页签存在、控件齐、找不到用例表时给提示而不是抛异常。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from rpa_core.gui.command_matrix import (
    MATRIX_ENV,
    MATRIX_ENV_HINT,
    REPORT_ENV,
    MatrixState,
    build_pytest_command,
    describe_namespaces,
    find_repo_root,
    load_case_table,
    read_new_lines,
    total_variants,
)
from tests.commands.conftest import split_variant_node

REPO_ROOT = Path(__file__).resolve().parents[2]
# 一个真实存在的变体（收窄到它，让合同测试只跑一次用例）。
# 用**完整 nodeid** 指定而不是 `-k`：`-k` 的表达式语法会把 `::` / `-` 当运算符或分隔符。
ONE_VARIANT = "browser.closeTabs::all-true-closes-current-window"
ONE_VARIANT_NODE = (
    "tests/commands/test_browser_matrix.py"
    f"::test_command_variant[{ONE_VARIANT}]"
)


# ---- 1. 纯函数 -----------------------------------------------------------


def test_find_repo_root_locates_the_case_table():
    root = find_repo_root()
    assert root is not None
    assert (root / "tests" / "commands" / "cases" / "browser.json").is_file()


def test_describe_namespaces_reads_cases_and_lists_pending(tmp_path):
    """已建表的命名空间来自用例表；有命令目录但没有用例表的算「未建表」。"""
    cases = tmp_path / "tests" / "commands" / "cases"
    cases.mkdir(parents=True)
    (cases / "browser.json").write_text(
        json.dumps(
            {
                "browser.click": {"variants": [{"name": "a"}, {"name": "b"}]},
                "browser.close": {"variants": [{"name": "c"}]},
            }
        ),
        encoding="utf-8",
    )
    for name in ("browser", "desktop", "data"):
        (tmp_path / "commands" / name).mkdir(parents=True)

    built, pending = describe_namespaces(tmp_path)

    assert [(info.name, info.commands, info.variants) for info in built] == [
        ("browser", 2, 3)
    ]
    assert pending == ["data", "desktop"]  # browser 已建表，不进待建
    assert total_variants(tmp_path) == 3


def test_describe_namespaces_tolerates_broken_case_file(tmp_path):
    """坏 JSON 只是少一个命名空间，不该让整个页面炸掉（页面是只读消费者）。"""
    cases = tmp_path / "tests" / "commands" / "cases"
    cases.mkdir(parents=True)
    (cases / "browser.json").write_text("{ not json", encoding="utf-8")

    built, _ = describe_namespaces(tmp_path)

    assert built == []
    assert load_case_table(tmp_path) == {}


def test_pytest_argv_shape_and_no_invented_flags():
    """argv 里不能有自己编的选项——报告路径走环境变量（回归守卫：曾误加 `--report=`）。"""
    argv = build_pytest_command("python")

    assert argv[:3] == ["python", "-m", "pytest"]
    assert "tests/commands" in argv  # 只跑矩阵目录，不跑全量套件
    assert "-p" in argv and "no:cacheprovider" in argv  # 别反复点出一堆 .pytest_cache
    assert not [arg for arg in argv if arg.startswith("--report")]


def test_env_names_match_the_pytest_side():
    """面板与 pytest 钩子各持一份环境变量名——**一份约定两处副本**，钉住不许漂。

    漂了的后果是静默的：面板把 `RPA_COMMAND_MATRIX_REPORT` 注进子进程、钩子却读另一个名字，
    于是子进程一个字节都不写，页面只会显示「运行结束但没有结果记录」——看上去像 pytest 没装。

    **2026-09-23 扩到三个开关**（页签集成桌面与 L2）：`RPA_DESKTOP_E2E` / `RPA_BROWSER_L2`
    同样是一份约定两处副本，且漂了同样是静默的——面板注入了 `RPA_DESKTOP_E2E`、
    驱动读的却是别的名字，按钮二点下去会**安静地跑成按钮一**（桌面那 190 个变体全 skip，
    页面照样报「完成」）。所以三个名字都钉。
    """
    from rpa_core.gui import command_matrix as panel_module
    from tests.commands import conftest as matrix_conftest
    from tests.commands import desktop_harness

    assert REPORT_ENV == matrix_conftest.REPORT_ENV
    assert MATRIX_ENV == matrix_conftest.MATRIX_ENV
    assert MATRIX_ENV_HINT == matrix_conftest.MATRIX_ENV_HINT
    # 面板的 L2 常量 vs conftest 与 L2 驱动各自读的名字
    assert panel_module.L2_ENV == matrix_conftest.L2_ENV
    # 面板的桌面常量 vs 桌面装配模块读的名字
    assert panel_module.DESKTOP_ENV == desktop_harness.DESKTOP_ENV
    # 三个名字互不相同（复制粘贴时最容易把两者写成同一个，那样按钮就会串味）
    assert len({panel_module.MATRIX_ENV, panel_module.DESKTOP_ENV, panel_module.L2_ENV}) == 3


def test_read_new_lines_is_incremental_and_keeps_half_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"event": "case", "outcome": "passed"}\n', encoding="utf-8")

    first, offset = read_new_lines(path, 0)
    assert [item["outcome"] for item in first] == ["passed"]

    # 空读：偏移不动
    again, offset = read_new_lines(path, offset)
    assert again == []

    # 半行不消费（否则那一条结果会永久缺失）
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"event": "case", "outcome": "fail')
    partial, offset = read_new_lines(path, offset)
    assert partial == []

    # 补齐后能读到
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('ed"}\n')
    rest, _ = read_new_lines(path, offset)
    assert [item["outcome"] for item in rest] == ["failed"]


def test_read_new_lines_skips_garbage_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        '{"event": "case", "outcome": "passed"}\nnot json\n{"event": "case"}\n',
        encoding="utf-8",
    )

    records, _ = read_new_lines(path, 0)

    assert len(records) == 2


def test_split_variant_node_shapes():
    node = f"tests/commands/test_browser_matrix.py::test_command_variant[{ONE_VARIANT}]"
    assert split_variant_node(node) == ("browser.closeTabs", "all-true-closes-current-window")
    assert split_variant_node("tests/contract/test_x.py::test_other") is None
    # 变体名里带 :: 时按第一个切（命令 id 不含 ::）
    assert split_variant_node("x::test_command_variant[a.b::c::d]") == ("a.b", "c::d")


def test_matrix_state_counts_and_failures():
    state = MatrixState()
    for record in (
        {"event": "case", "command": "a", "outcome": "passed"},
        {"event": "case", "command": "a", "outcome": "failed"},
        {"event": "case", "command": "b", "outcome": "skipped"},
    ):
        state.apply(record)

    assert state.done == 3
    assert state.failed == 1
    assert [record["command"] for record in state.failures()] == ["a"]
    assert state.describe(total=10) == "已跑 3 / 10 · 通过 1 · 失败 1 · 跳过 1"

    # 收尾的 summary 事件覆盖计数（它是被测方给的权威值）
    state.apply({"event": "summary", "counts": {"passed": 4}})
    assert (state.done, state.failed) == (4, 0)
    assert state.describe() == "已跑 4 · 通过 4"


# ---- 2. 实时报告钩子（真跑一个变体） --------------------------------------


def test_live_report_hook_writes_case_and_summary(tmp_path):
    """`RPA_COMMAND_MATRIX_REPORT` 一设，被测方就要吐出可增量读的 JSONL。"""
    report = tmp_path / "report.jsonl"
    env = {
        **os.environ,
        MATRIX_ENV: "1",
        REPORT_ENV: str(report),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", ONE_VARIANT_NODE,
            "-p", "no:cacheprovider",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, check=False,
    )
    assert proc.returncode == 0, proc.stdout[-2000:]

    records, _ = read_new_lines(report, 0)
    cases = [item for item in records if item.get("event") == "case"]
    summaries = [item for item in records if item.get("event") == "summary"]

    assert len(cases) == 1
    assert cases[0]["command"] == "browser.closeTabs"
    assert cases[0]["variant"] == "all-true-closes-current-window"
    assert cases[0]["outcome"] == "passed"
    assert cases[0]["durationMs"] > 0
    assert len(summaries) == 1
    assert summaries[0]["counts"] == {"passed": 1}
    assert summaries[0]["exitStatus"] == 0


def test_live_report_is_off_without_env(tmp_path):
    """缺省不产出任何文件——命令行手工跑不该被塞一份报告。"""
    report = tmp_path / "absent.jsonl"
    env = {**os.environ, MATRIX_ENV: "1", "PYTHONIOENCODING": "utf-8"}
    env.pop(REPORT_ENV, None)
    subprocess.run(
        [sys.executable, "-m", "pytest", ONE_VARIANT_NODE, "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env, check=False,
    )

    assert not report.exists()


# ---- 3. 页签装配 ---------------------------------------------------------

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


def _home(qapp, tmp_path):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root
    from rpa_core.devserver.store import WorkflowDirStore
    from rpa_core.gui.home import HomeWindow

    store = WorkflowDirStore(tmp_path / "workflows")
    return HomeWindow(store, load_catalog(_commands_root()))


def test_workbench_has_command_matrix_tab(qapp, tmp_path):
    home = _home(qapp, tmp_path)

    titles = [home.tabs.tabText(i) for i in range(home.tabs.count())]
    assert titles == ["流程库", "运行历史", "指令测试"]
    assert home.matrix_panel is home.tabs.widget(2)
    assert home.matrix_panel.run_button.text() == "运行 L1 契约矩阵"
    assert home.matrix_panel.static_button.isEnabled()

    home.matrix_panel.shutdown()


def test_panel_scope_mentions_covered_and_pending(qapp, tmp_path):
    """覆盖范围来自用例表，未建表的命名空间也要写出来（免得问「怎么只有浏览器」）。"""
    home = _home(qapp, tmp_path)
    text = home.matrix_panel.scope_label.text()

    assert "browser" in text
    assert "32 条命令" in text
    assert "180 个变体" in text
    assert "desktop" in text and "data" in text and "workflow" in text

    home.matrix_panel.shutdown()


def test_scope_text_states_what_the_button_actually_runs(qapp, tmp_path):
    """「已覆盖」不能只列规模，必须说清**每个按钮点下去实际跑什么**（2026-09-23）。

    防的是一条具体的误读：`scope_label` 列了全部 5 个命名空间的变体数，读起来像「这些都会跑」。
    页签现在有三个目标（L1 / L1+桌面 / L1+L2），文案要把**副作用面**逐条讲清
    （不碰浏览器 / 抢前台 / 拉起真浏览器）——三者差别很大，点的人必须能事先知道。
    """
    from rpa_core.gui.command_matrix import scope_breakdown

    home = _home(qapp, tmp_path)
    text = home.matrix_panel.scope_label.text()
    scope = scope_breakdown(REPO_ROOT)

    # 三笔账都非零（否则这条断言自己会被空数据喂成假绿）
    assert scope.matrix_core > 0 and scope.desktop > 0 and scope.l2 > 0

    assert f"按钮一（L1）跑 {scope.matrix_core} 个变体" in text
    assert f"按钮二（L1 + 桌面真机）再加 {scope.desktop} 个桌面变体" in text
    assert "抢前台" in text  # 桌面目标的副作用面必须写明
    assert f"按钮三（L1 + 浏览器真机）再加 {scope.l2} 个 L2 块" in text
    assert "拉起一个真实浏览器窗口" in text  # L2 的副作用面必须写明

    home.matrix_panel.shutdown()


def test_targets_map_to_the_three_switches(qapp, tmp_path):
    """三个目标按钮存在，且各自的环境变量组合正确（`RUN_TARGETS` 与 conftest 是一份约定）。"""
    from rpa_core.gui.command_matrix import L2_ENV, MATRIX_ENV, RUN_TARGETS, find_target

    home = _home(qapp, tmp_path)
    panel = home.matrix_panel

    assert len(RUN_TARGETS) == 3
    assert set(panel.target_buttons) == {t.key for t in RUN_TARGETS}
    for target in RUN_TARGETS:
        # 每个目标都有 L1（L2/桌面是**叠加**在 L1 上的，不是替代）
        assert target.env[MATRIX_ENV] == "1"
        assert target.note  # 副作用面必须写出来，不能空

    l1, l1_desktop, l1_l2 = RUN_TARGETS
    assert not l1.runs_desktop and not l1.runs_l2
    assert l1_desktop.runs_desktop and not l1_desktop.runs_l2
    assert l1_l2.runs_l2 and not l1_l2.runs_desktop
    assert L2_ENV in l1_l2.env

    with pytest.raises(KeyError):
        find_target("nosuchtarget")

    panel.shutdown()


def test_collection_matrix_is_additive():
    """收集口径四格（2026-09-23 页签集成的前置，M38 §1.15）。

    这是那个「L1 + L2」目标能不能工作的**根**：改前 `RPA_COMMAND_MATRIX` 一开就会
    ignore 掉全部 L1 驱动、`RPA_BROWSER_L2` 一开就 ignore 掉除 L2 之外的全部，
    两个开关**互斥**，所以「一起跑」这条路不存在。本测试把四格钉住。

    `existing` 注入固定清单：断言不随目录里新增/删除驱动文件而漂。
    """
    from tests.commands.conftest import L2_DRIVER_NAME, collection_ignore

    existing = [
        "test_browser_matrix.py",
        "test_browser_l2_matrix.py",
        "test_data_matrix.py",
        "test_desktop_matrix.py",
        "test_desktop_win32_matrix.py",
    ]
    l1_drivers = [n for n in existing if n != L2_DRIVER_NAME]

    def _kept(matrix: bool, l2: bool) -> set[str]:
        ignored = set(collection_ignore(matrix_enabled=matrix, l2_enabled=l2, existing=existing))
        return set(existing) - ignored

    # 都不开：什么都不收集
    assert _kept(False, False) == set()
    # 只开 L2：只有 L2 驱动（旧口径保持，单独跑真机冒烟）
    assert _kept(False, True) == {L2_DRIVER_NAME}
    # 只开 MATRIX：全部 L1 驱动，**不含** L2 驱动
    assert _kept(True, False) == set(l1_drivers)
    # 两个都开：叠加（这一格是本次新开的）
    assert _kept(True, True) == set(l1_drivers) | {L2_DRIVER_NAME}

    # 只要 L1 在收集（`matrix=True`），桌面驱动就**总在**名单里——它们是否真跑
    # 由 `RPA_DESKTOP_E2E` 在驱动内部把关，收集层不掺和（这正是页签按钮二能生效的前提）。
    for l2 in (False, True):
        kept = _kept(True, l2)
        for name in existing:
            if "desktop" in name:
                assert name in kept, (l2, name)


def test_progress_total_follows_the_chosen_target(qapp, tmp_path):
    """进度条分母随目标变（不是恒等于全表）——否则选 L1 时分母会大到永远填不满。"""
    from rpa_core.gui.command_matrix import RUN_TARGETS, scope_breakdown

    home = _home(qapp, tmp_path)
    panel = home.matrix_panel
    scope = scope_breakdown(REPO_ROOT)

    for target in RUN_TARGETS:
        panel._target = target
        expected = scope.matrix_core
        if target.runs_desktop:
            expected += scope.desktop
        if target.runs_l2:
            expected += scope.l2
        assert panel._progress_total() == expected, target.key

    panel.shutdown()


def test_run_matrix_really_injects_the_target_env(qapp, tmp_path, monkeypatch):
    """**中间态断言**：`_run_matrix` 真把目标的开关注进子进程了（不只是数据里有）。

    这条防的是一个**实测抓到过的假绿灯**（2026-09-23）：只断言 `RUN_TARGETS[i].env` 的内容
    时，把 `_run_matrix` 里的 `for name, value in self._target.env.items()` 整段摘掉
    （退回「只注 MATRIX」），**契约测试 20 项全绿一个不红**——因为数据定义没动。
    后果是安静的：按钮二点下去会跑成按钮一（桌面 190 个变体全 skip，页面照样报「完成」）。

    所以这里断言的是**子进程真实持有的环境**（`processEnvironment()`），
    也就是「目标选择」这条链路的必经中间态。只打桩 `_start`（argv 组装那一半），
    环境由面板自己组装。
    """
    from rpa_core.gui.command_matrix import DESKTOP_ENV, L2_ENV, MATRIX_ENV, RUN_TARGETS

    home = _home(qapp, tmp_path)
    panel = home.matrix_panel
    seen: list[dict[str, str]] = []

    def _fake_start(_argv, _mode):
        env = panel._process.processEnvironment()
        seen.append(
            {
                "matrix": env.value(MATRIX_ENV),
                "desktop": env.value(DESKTOP_ENV),
                "l2": env.value(L2_ENV),
            }
        )

    monkeypatch.setattr(panel, "_start", _fake_start)

    for target in RUN_TARGETS:
        panel._target = target
        panel._on_target_clicked(target.key)

    assert len(seen) == len(RUN_TARGETS)
    for target, env in zip(RUN_TARGETS, seen, strict=True):
        assert env["matrix"] == "1", target.key  # 三个目标都含 L1
        assert env["desktop"] == target.env.get(DESKTOP_ENV, ""), target.key
        assert env["l2"] == target.env.get(L2_ENV, ""), target.key

    # 关键区分：三个目标注进去的东西**互不相同**（否则「选择目标」是装饰）
    assert len({tuple(sorted(item.items())) for item in seen}) == len(RUN_TARGETS)

    panel.shutdown()


def test_scope_breakdown_matches_the_case_tables():
    """三笔账与用例表同源：`core + desktop + l2` 的划分按**文件名 + 变体是否带 l2 块**。

    这条不读死数字（表会长），只钉划分规则本身——桌面两表进 `desktop`、
    带 `l2` 块的进 `l2`（同时仍计入 `core`，因为它在本页确实会被收集）。
    """
    from rpa_core.gui.command_matrix import scope_breakdown

    scope = scope_breakdown(REPO_ROOT)
    table = load_case_table(REPO_ROOT)

    # 桌面两表的划分按**用例表文件名**（`desktop` / `desktop_win32`），不是命令 id 前缀——
    # `desktop.win32.click` 的命令 id 前三段也长得像 `desktop.*`，按前缀分会重复计数
    # （本文件这条测试自己就先踩了一次，见 M38 任务单 §1.14）。
    desktop_files = ("desktop", "desktop_win32")
    desktop_variants = sum(
        len(spec.get("variants") or [])
        for name, spec in table.items()
        if name.startswith("desktop.")
    )
    # 用「命令 id 是否以 `desktop.win32.` 开头」把两半拆开复核，两者相加须等于桌面总数
    win32_only = sum(
        len(spec.get("variants") or [])
        for name, spec in table.items()
        if name.startswith("desktop.win32.")
    )
    uia_only = desktop_variants - win32_only
    assert scope.desktop == desktop_variants
    assert uia_only + win32_only == desktop_variants
    assert uia_only > 0 and win32_only > 0  # 两半都非零，否则上面的减法会把空数据喂成假绿
    assert len(desktop_files) == 2  # 与 `DESKTOP_NAMESPACES` 的成员数一致（改了记得同步）

    l2_variants = sum(
        1
        for spec in table.values()
        for variant in (spec.get("variants") or [])
        if variant.get("l2")
    )
    assert scope.l2 == l2_variants

    # `l2` 是本页仍会收集的变体（L1 侧照跑），故它是 `core` 的**子集**而不是并列项——
    # 所以总数是 `core + desktop`，**不能**再加一遍 `l2`（那会双重计数）。
    assert 0 < scope.l2 < scope.matrix_core
    assert scope.total == scope.matrix_core + scope.desktop
    assert scope.total == sum(len(spec.get("variants") or []) for spec in table.values())


def test_missing_repo_root_reports_instead_of_spawning(qapp, tmp_path):
    """安装态（无用例表）点运行只给提示，不起子进程——不能静默什么都不做。"""
    from PySide6.QtCore import QProcess

    from rpa_core.gui.command_matrix import CommandMatrixPanel

    panel = CommandMatrixPanel(repo_root=None)
    panel._root = None  # 强制「找不到仓库根」这一支
    panel._on_run_clicked()

    assert panel._process.state() == QProcess.ProcessState.NotRunning
    assert "找不到用例表目录" in panel.status.text()
    panel.shutdown()


def test_panel_streams_jsonl_into_rows(qapp, tmp_path, monkeypatch):
    """白盒集成：走**真实入口** `_on_run_clicked` → `_run_matrix`，子进程边写 JSONL，页签边出结果。

    子进程是一段脚本（不嵌套真跑矩阵），它**只从环境变量 `RPA_COMMAND_MATRIX_REPORT` 取落盘
    路径**——与 `tests/commands/conftest.py` 的钩子同一份约定，所以这一条顺带证明「页面把报告
    路径真的注进了子进程环境」。只有 argv 那一半打桩（替换 `build_pytest_command`），其余全走
    真实实现：报告文件创建、轮询点火、增量读、表格、状态文字、按钮复位。

    **为什么必须观察到「进程还在跑时就已经有行」**：`_on_finished` 收尾时还会 `_poll_report()`
    一次，所以「结束后有行」证明不了轮询真的点火——摘掉 `_run_matrix` 里的 `_timer.start()`
    这个用例照样绿（负向验证实测到的假绿灯）。只有中间态能把它钉住，故子进程写完第一行后
    **等测试放行**才继续。
    """
    import time as _time

    from PySide6.QtCore import QProcess

    from rpa_core.gui import command_matrix as module
    from rpa_core.gui.command_matrix import CommandMatrixPanel

    gate = tmp_path / "gate"
    script = (
        "import json, os, pathlib, sys, time\n"
        "path = pathlib.Path(os.environ['RPA_COMMAND_MATRIX_REPORT'])\n"
        "gate = pathlib.Path(sys.argv[1])\n"
        "def emit(record):\n"
        "    with path.open('a', encoding='utf-8', newline='\\n') as stream:\n"
        "        stream.write(json.dumps(record) + '\\n')\n"
        "        stream.flush()\n"
        "emit({'event': 'case', 'command': 'a.x', 'variant': 'one',\n"
        "      'outcome': 'passed', 'durationMs': 1.0})\n"
        "deadline = time.time() + 60\n"
        "while not gate.exists() and time.time() < deadline:\n"
        "    time.sleep(0.02)\n"
        "emit({'event': 'case', 'command': 'b.y', 'variant': 'two',\n"
        "      'outcome': 'failed', 'durationMs': 2.0})\n"
        "emit({'event': 'summary', 'collected': 2, 'exitStatus': 1,\n"
        "      'counts': {'passed': 1, 'failed': 1}})\n"
    )
    monkeypatch.setattr(
        module,
        "build_pytest_command",
        lambda _python: [sys.executable, "-c", script, str(gate)],
    )
    panel = CommandMatrixPanel(repo_root=REPO_ROOT)
    panel._report_dir = tmp_path / "reports"

    def pump_until(predicate, timeout: float) -> bool:
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            qapp.processEvents()
            if predicate():
                return True
            _time.sleep(0.02)
        return False

    panel._on_run_clicked()

    assert panel._timer.isActive(), "轮询没有点火（`_run_matrix` 漏了 `_timer.start()`）"
    assert panel._mode == "matrix"
    # 实时性：子进程还卡在 gate 上时，第一行就该出到表里——这一条才证明轮询真在工作
    assert pump_until(lambda: panel.results.topLevelItemCount() >= 1, 30.0), (
        "子进程还在跑，结果行却没实时出现——轮询没接上"
    )
    assert panel._process.state() != QProcess.ProcessState.NotRunning, (
        "子进程已经结束——实时性无从证明（用例前提被破坏，检查 gate 是否失效）"
    )

    gate.write_text("go", encoding="utf-8")
    assert pump_until(lambda: panel._mode is None, 60.0), "子进程结束后页签没有回到空闲态"

    assert panel._process.state() == QProcess.ProcessState.NotRunning
    assert panel.results.topLevelItemCount() == 2
    first, second = (
        panel.results.topLevelItem(0),
        panel.results.topLevelItem(1),
    )
    assert (first.text(0), first.child(0).text(0)) == ("a.x", "one")
    assert first.child(0).text(1) == "通过"
    assert (second.text(0), second.child(0).text(0)) == ("b.y", "two")
    assert second.child(0).text(1) == "失败"
    assert "有失败" in panel.status.text()
    assert panel.progress.value() == 2
    assert panel.run_button.text() == "运行 L1 契约矩阵"
    # 报告由子进程按**环境变量里的路径**写：顺带证明页面真的注了 REPORT_ENV
    records, _ = read_new_lines(panel._report_path, 0)
    assert [record.get("event") for record in records] == ["case", "case", "summary"]

    panel.shutdown()


def test_shutdown_kills_running_child(qapp, tmp_path, monkeypatch):
    """关窗口/关页签时要收掉子进程——否则留下一个还在跑矩阵的孤儿 Python（M34 同类）。

    `HomeWindow.closeEvent` → `matrix_panel.shutdown()` 是这条命令唯一的用户可见出口：
    它坏掉的表现是「窗口关了，后台 pytest 还在跑」，比界面出错更难发现。
    """
    import time as _time

    from PySide6.QtCore import QProcess

    from rpa_core.gui import command_matrix as module
    from rpa_core.gui.command_matrix import CommandMatrixPanel

    monkeypatch.setattr(
        module,
        "build_pytest_command",
        lambda _python: [sys.executable, "-c", "import time; time.sleep(120)"],
    )
    panel = CommandMatrixPanel(repo_root=REPO_ROOT)
    panel._report_dir = tmp_path / "reports"
    panel._on_run_clicked()

    def wait_for(predicate, timeout: float) -> bool:
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            qapp.processEvents()
            if predicate():
                return True
            _time.sleep(0.02)
        return False

    assert wait_for(
        lambda: panel._process.state() != QProcess.ProcessState.NotRunning, 30.0
    ), "子进程没起来"

    panel.shutdown()

    assert wait_for(
        lambda: panel._process.state() == QProcess.ProcessState.NotRunning, 15.0
    ), "shutdown 之后子进程还在跑（会留下孤儿）"
    assert not panel._timer.isActive()
