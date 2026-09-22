"""桌面通道的「运行 → 暂停 → 继续」真机验证（M38 S2.1）。

维护者定案（2026-09-22）：S2 走**真桌面 fixture**，且要补的就是这条证据——此前
「暂停后继续」的真机证据只在**浏览器通道**（M21，macOS + Edge），桌面通道没验过。

**为什么这个用例必须跨进程**：暂停 = 干净收口 + 进程退出（ADR 0005），而 GUI 的
「继续」按钮在暂停已落地时走的是 `RunManager.resume` → `rpa-core resume`
**起一个新进程**（`devserver/runs.py::continue_run`）。所以「续接」这件事只在
跨进程时才有内容可证：窗口不随 run 进程退出而消失，消失的是新进程里的句柄表。

**断言三件事**：

1. **续接的是同一个窗口**——续跑段里点一次 `submitButton`，让它按 fixture 自己的逻辑
   把**输入框里的内容**抄到 `resultText`（`Program.cs::GenerateGreeting`），再把
   `resultText` 读回来，应当等于**暂停之前**敲进去的那句话；若新进程重新附着/新建了
   窗口，输入框是空的，抄过去就是空串。
   *为什么绕这一下*：`resultText` 只在点提交时被赋值，不点就永远是初始值 `Ready`
   ——第一版按「输入框镜像到 resultText」写判据，实测直接红在
   `assert 'Ready' == 'hello rpa, ...'`；而直接读输入框自己又要押注「`getText` 对
   UIA Edit 也返回文本」这条没实测过的假设。借 fixture 的提交处理程序当读侧探针，
   两个假设都不需要。
2. **会话与元素都接回来了**——暂停点之后第一个命令是 `click`，它同时引用暂停前的
   `attachWindow` 的 sessionId 与暂停前 `findElement` 的 elementId（两处缺口实测过：
   只补会话会从 `SESSION_NOT_FOUND` 变成 `ELEMENT_NOT_FOUND`）；
3. **已完成节点零重跑**——点的是**非幂等**的计数器按钮：重跑一次读数就是 `2`。
   另从 events 的续跑段断言「暂停前的节点一个都没再出现」（不靠推断，靠事件流）。

真机边界（如实记录）：这里只跑 **UIA** 后端。win32 后端的 `restore_from_scopes` 有
契约测试（`tests/unit/test_desktop_session_restore.py`），但**没有**对应的真机用例——
win32 定位器认的是 title/class/controlId，而本 fixture 的控件是 `Name`（UIA AutomationId），
拿 win32 后端定位它们要另做一套靶子，属独立切片。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import desktop_fixture
import pytest

from rpa_core.control_channel import request_pause

ROOT = Path(__file__).resolve().parents[2]

_UNAVAILABLE = desktop_fixture.fixture_unavailable_reason()
requires_uia_fixture = pytest.mark.skipif(_UNAVAILABLE is not None, reason=_UNAVAILABLE or "")

# 本文件会弹窗抢前台——缺省跳过（见 tests/conftest.py）
pytestmark = pytest.mark.skipif(
    os.environ.get("RPA_DESKTOP_E2E") != "1",
    reason="真实桌面 E2E 会弹窗抢焦点，缺省跳过；设 RPA_DESKTOP_E2E=1 启用",
)

# 敲进输入框的那句话要**足够长**：`keyIntervalMs` 是逐字间隔，它就是本用例的
# 「确定性慢节点」——暂停的生效点是节点边界，需要一个够长的窗口让暂停请求落进来。
# 41 字 × 80ms ≈ 3.3s（实测 3735ms）。
TYPED_TEXT = "hello rpa, this is the desktop pause probe"
KEY_INTERVAL_MS = 80

# 暂停应当落在哪个节点之前：`typeGreeting` 之后、`clickCount` 之前。
PAUSE_BEFORE_NODE = "clickCount"
# 暂停前已经完成的节点——续跑段里**不允许**再出现任何一个。
PRE_PAUSE_NODES = {"attachMain", "findInput", "findCount", "typeGreeting"}
POST_PAUSE_NODES = {
    "clickCount",
    "findCountLabel",
    "readCount",
    "findSubmit",
    "clickSubmit",
    "findResult",
    "readResult",
}


def build_workflow() -> dict:
    """暂停点刻意设在 `typeGreeting` 之后（见模块 docstring 的第 2、3 条断言）。"""
    sid = "${steps.attachMain.outputs.sessionId}"
    return {
        "schema_version": "1.0",
        "id": "desktop-pause-resume",
        "name": "desktop pause and continue",
        "inputs": {"title": desktop_fixture.APP_TITLE},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {
                    "type": "action",
                    "id": "attachMain",
                    "command": "desktop.attachWindow",
                    "with": {"title": "${inputs.title}", "timeoutMs": 5000},
                },
                {
                    "type": "action",
                    "id": "findInput",
                    "command": "desktop.findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "queryInput", "controlType": "Edit"},
                    },
                },
                {
                    "type": "action",
                    "id": "findCount",
                    "command": "desktop.findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "countButton", "controlType": "Button"},
                    },
                },
                {
                    "type": "action",
                    "id": "typeGreeting",
                    "command": "desktop.input",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findInput.outputs.elementId}",
                        "text": TYPED_TEXT,
                        "keyIntervalMs": KEY_INTERVAL_MS,
                    },
                },
                {
                    "type": "action",
                    "id": "clickCount",
                    "command": "desktop.click",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findCount.outputs.elementId}",
                    },
                },
                {
                    "type": "action",
                    "id": "findCountLabel",
                    "command": "desktop.findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "countLabel"},
                    },
                },
                {
                    "type": "action",
                    "id": "readCount",
                    "command": "desktop.getText",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findCountLabel.outputs.elementId}",
                    },
                },
                # 「同一窗口」的读侧探针：点提交 → fixture 把**输入框当前内容**抄进
                # `resultText` → 再读回来。不点提交，`resultText` 永远是初始值 `Ready`。
                {
                    "type": "action",
                    "id": "findSubmit",
                    "command": "desktop.findElement",
                    "with": {
                        "sessionId": sid,
                        "locator": {"automationId": "submitButton", "controlType": "Button"},
                    },
                },
                {
                    "type": "action",
                    "id": "clickSubmit",
                    "command": "desktop.click",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findSubmit.outputs.elementId}",
                    },
                },
                {
                    "type": "action",
                    "id": "findResult",
                    "command": "desktop.findElement",
                    "with": {"sessionId": sid, "locator": {"automationId": "resultText"}},
                },
                {
                    "type": "action",
                    "id": "readResult",
                    "command": "desktop.getText",
                    "with": {
                        "sessionId": sid,
                        "elementId": "${steps.findResult.outputs.elementId}",
                    },
                },
                {"type": "return", "id": "done", "value": "${steps.readCount.outputs.value}"},
            ],
        },
    }


class _CliRun:
    """跑一个 `rpa_core.cli` 子进程并实时收集 stdout 行（真实 run 进程 = 真跨进程）。"""

    def __init__(self, args: list[str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "rpa_core.cli", *args],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_run_id(self, timeout: float = 30.0) -> str:
        """CLI 在 run 启动后立即打印单行 `{"run_id": ...}`（flush），据此尽早拿到 run 目录。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self.lines):
                stripped = line.strip()
                if not stripped.startswith("{") or '"run_id"' not in stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except ValueError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("run_id"), str):
                    return payload["run_id"]
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"未从 CLI 输出解析出 run_id：{self.lines[-6:]}")

    def wait(self, timeout: float = 120.0) -> int:
        self._reader.join(timeout)
        return self.proc.wait(timeout=timeout)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _completed_leaves(run_dir: Path) -> set[str]:
    """检查点里的已完成节点，取**叶子名**。

    快照里的键是路径键 `root/attachMain`（`_node_path_key`）。第一版探针拿裸名做子集
    判断，于是判据永远不成立、一路跑到进程退出——暂停请求落在收口之后 111ms（实测）。
    """
    checkpoint = _read_json(run_dir / "checkpoint.json")
    return {str(step).rsplit("/", 1)[-1] for step in checkpoint.get("completedSteps") or []}


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    parsed = []
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except ValueError:
            continue
    return parsed


def _resumed_segment(run_dir: Path) -> list[dict]:
    """`runResumed` 之后的事件（续跑段）。事件文件跨暂停/继续共用，靠这个标记切分。"""
    events = _events(run_dir)
    for index, event in enumerate(events):
        if event.get("type") == "runResumed":
            return events[index + 1 :]
    return []


@requires_uia_fixture
def test_desktop_pause_then_continue_keeps_window_and_does_not_replay(tmp_path):
    artifacts = tmp_path / "artifacts"
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(build_workflow(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    exe = desktop_fixture.compile_demo_app(tmp_path)
    desktop_fixture.kill_demo_apps()
    fixture = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        # `type_keys` 走 SendInput，落到前台窗口——先把焦点抢回来
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)

        # ---- 跑起来，并在慢节点期间请求暂停 --------------------------------
        run = _CliRun(["run", str(workflow_path), "--artifacts", str(artifacts)])
        run_id = run.wait_run_id()
        run_dir = artifacts / run_id

        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            # 暂停的生效点是节点边界：等慢节点之前的节点全部完成再写请求，
            # 请求就会落在 `typeGreeting` 之后、`clickCount` 之前。
            if {"attachMain", "findInput", "findCount"}.issubset(_completed_leaves(run_dir)):
                break
            assert run.proc.poll() is None, (
                "run 在请求暂停前就结束了——判据没打中慢节点，"
                f"已完成：{sorted(_completed_leaves(run_dir))}"
            )
            time.sleep(0.05)
        assert request_pause(run_dir)["pause"] is True

        run.wait()
        paused = _read_json(run_dir / "result.json")
        assert paused.get("status") == "paused", paused
        paused_events = [event for event in _events(run_dir) if event.get("type") == "runPaused"]
        assert paused_events, "缺少 runPaused 事件"
        assert paused_events[-1]["payload"]["nodeId"] == PAUSE_BEFORE_NODE
        assert paused_events[-1]["payload"]["reason"] == "user"
        assert PRE_PAUSE_NODES.issubset(_completed_leaves(run_dir))

        # ---- 「继续」= 新进程（与 GUI 的 continue_run 同一条路） --------------
        resume = _CliRun(
            [
                "resume",
                str(workflow_path),
                "--run-id",
                run_id,
                "--artifacts",
                str(artifacts),
            ]
        )
        assert resume.wait() == 0, resume.lines[-10:]

        resumed = _read_json(run_dir / "result.json")
        assert resumed.get("status") == "succeeded", resumed
        steps = resumed.get("outputs") or {}

        # 1) 零重跑：计数器只被点了一次（重跑会变 "2"）
        assert resumed["return_value"] == "1"
        # 2) 续接同一窗口：点提交后读回来的仍是暂停之前敲进去的那句话
        #    （新窗口的输入框是空的，抄过去就是空串）
        assert steps["readResult"]["outputs"]["value"] == TYPED_TEXT
        # 3) 零重跑的事件级证据：续跑段只出现暂停点之后的节点
        resumed_nodes = {
            str(event.get("node_id"))
            for event in _resumed_segment(run_dir)
            if event.get("type") == "stepCompleted"
        }
        assert resumed_nodes == POST_PAUSE_NODES
        assert not (resumed_nodes & PRE_PAUSE_NODES)
    finally:
        fixture.terminate()
        try:
            fixture.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fixture.kill()
            fixture.wait(timeout=10)
        desktop_fixture.kill_demo_apps()


@requires_uia_fixture
def test_pause_resume_workflow_shape_is_deterministic():
    """流程形状钉死：暂停点前后各自用到的东西是刻意安排的（见模块 docstring）。"""
    children = build_workflow()["root"]["children"]
    commands = [child["command"] for child in children if "command" in child]
    assert commands == [
        "desktop.attachWindow",
        "desktop.findElement",
        "desktop.findElement",
        "desktop.input",
        "desktop.click",
        "desktop.findElement",
        "desktop.getText",
        "desktop.findElement",
        "desktop.click",
        "desktop.findElement",
        "desktop.getText",
    ]
    # 暂停点之后的 `click` 必须引用暂停**之前**两个节点的产物，
    # 否则这个用例就测不到会话/元素还原（改动流程的人应该在这里被拦下）。
    click_node = next(child for child in children if child["id"] == "clickCount")
    assert click_node["with"]["sessionId"] == "${steps.attachMain.outputs.sessionId}"
    assert click_node["with"]["elementId"] == "${steps.findCount.outputs.elementId}"
    order = [child["id"] for child in children]
    assert order.index("findCount") < order.index("typeGreeting") < order.index("clickCount")
    # 「同一窗口」的探针必须在**点提交之前**把输入框填好，且提交要在续跑段里
    # （顺序错了就不是在测续接，而是在测一次完整的全新运行）。
    assert order.index("typeGreeting") < order.index("clickSubmit") < order.index("readResult")
    assert PRE_PAUSE_NODES | POST_PAUSE_NODES == {child["id"] for child in children} - {"done"}
    assert not (PRE_PAUSE_NODES & POST_PAUSE_NODES)
