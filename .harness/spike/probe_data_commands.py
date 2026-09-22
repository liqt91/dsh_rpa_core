"""一次性探针（M38 S3 建表脚手架）：跑 data.* / workflow.* 命令，打印真实的
outputs / effects / 错误分支，并 dump 落盘文件内容。

用途：`tests/commands/cases/data.json` 与 `workflow.json` 里的期望值必须来自**实测**，
不是读代码猜出来的（策略 §5.1；S1 建表时实测纠正过 7 处期望）。

与 `probe_browser_commands.py` 的区别：数据通道**没有通道客户端可打桩**——
`PythonWorkerExecutor` 真的起一个 `python -m rpa_core.workers.python_worker` 子进程。
这对本通道不是缺陷而是最优解：命令全是纯 Python 文件/字符串操作，**真子进程就是真机**，
能一次性把「执行器下发了什么、worker 回了什么、磁盘上留下了什么」三件事都测到。

⚠️ 唯一危险面是**文件系统**：本探针只在系统临时目录里建工作区，所有 path 都在其中
（`workspace` = 本次运行的临时根，越界路径只用于负路径——实现会在 `_within_workspace`
处直接拒掉，不会落到磁盘操作）。用例表侧有同款安全阀，见 `tests/commands/test_data_matrix.py`。

不入产品代码、不进 lint 门禁（`.harness/spike/` 在 ruff exclude 里）。

用法：
    uv run python .harness/spike/probe_data_commands.py            # 全部
    uv run python .harness/spike/probe_data_commands.py data.format  # 指定命令
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rpa_core.executors.python_worker import PythonWorkerExecutor  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402

ROOT_TMP = Path(tempfile.mkdtemp(prefix="rpa-data-probe-"))
WORKSPACE = ROOT_TMP / "ws"
FLOW_DIR = ROOT_TMP / "flow"


def run(command: str, inputs: dict[str, Any], *, flow_dir: Path | None = None) -> dict:
    """跑一条命令（真子进程），返回可打印的结果摘要。"""
    payload = dict(inputs)
    if flow_dir is not None:
        payload.setdefault("flowDir", str(flow_dir))
    executor = PythonWorkerExecutor()
    invocation = CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="probe",
        step_id="step",
        inputs=payload,
    )
    started = time.perf_counter()
    result = asyncio.run(executor.execute(invocation, asyncio.Event()))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    summary = {
        "status": result.status,
        "outputs": result.outputs,
        "effects": [
            {"kind": effect.kind.value, "resource": effect.resource}
            for effect in result.effects
        ],
        "error": (
            None
            if result.error is None
            else {
                "code": result.error.code,
                "message": result.error.message,
                "details": result.error.details,
            }
        ),
        "elapsedMs": round(elapsed_ms, 1),
        "liveProcesses": executor.active_process_count,
    }
    asyncio.run(executor.close())
    return summary


def show(label: str, command: str, inputs: dict[str, Any], *, flow_dir: Path | None = None,
         files: list[Path] | None = None) -> None:
    summary = run(command, inputs, flow_dir=flow_dir)
    print(f"\n--- {label} :: {command}")
    print(f"    inputs = {json.dumps(inputs, ensure_ascii=False)}")
    print(f"    -> {json.dumps(summary, ensure_ascii=False)}")
    for path in files or []:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            print(f"    file[{path.name}] = {text!r}")
        else:
            print(f"    file[{path.name}] = <不存在>")


def main() -> None:
    wanted = sys.argv[1:] or None

    def want(command: str) -> bool:
        return wanted is None or command in wanted

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    note = WORKSPACE / "note.txt"
    note.write_text("原始内容", encoding="utf-8", newline="\n")

    print(f"tmp root = {ROOT_TMP}")

    if want("data.writeText"):
        show(
            "text 分支",
            "data.writeText",
            {"workspace": str(WORKSPACE), "path": "out/a.txt", "text": "你好\n世界"},
            files=[WORKSPACE / "out" / "a.txt"],
        )
        show(
            "lines 分支（数组按行拼）",
            "data.writeText",
            {"workspace": str(WORKSPACE), "path": "out/b.txt", "lines": ["第一行", "第二行"]},
            files=[WORKSPACE / "out" / "b.txt"],
        )
        show(
            "text 与 lines 同时给（oneOf 由 orchestrator 拦，执行器取 text）",
            "data.writeText",
            {"workspace": str(WORKSPACE), "path": "out/c.txt", "text": "T", "lines": ["L"]},
            files=[WORKSPACE / "out" / "c.txt"],
        )
        show(
            "绝对路径逃逸工作区",
            "data.writeText",
            {"workspace": str(WORKSPACE), "path": str(ROOT_TMP / "escape.txt"), "text": "x"},
        )
        show(
            "相对路径穿越（../）",
            "data.writeText",
            {"workspace": str(WORKSPACE), "path": "../escape2.txt", "text": "x"},
        )

    if want("data.appendText"):
        show(
            "追加到已存在文件",
            "data.appendText",
            {"workspace": str(WORKSPACE), "path": "note.txt", "text": "-追加"},
            files=[note],
        )
        show(
            "lines 分支",
            "data.appendText",
            {"workspace": str(WORKSPACE), "path": "note.txt", "lines": ["-L1", "-L2"]},
            files=[note],
        )
        show(
            "父目录不存在（自动 mkdir）",
            "data.appendText",
            {"workspace": str(WORKSPACE), "path": "deep/nested/x.txt", "text": "d"},
            files=[WORKSPACE / "deep" / "nested" / "x.txt"],
        )

    if want("data.readText"):
        show("读已存在文件", "data.readText", {"workspace": str(WORKSPACE), "path": "note.txt"})
        show("读不存在的文件", "data.readText", {"workspace": str(WORKSPACE), "path": "nope.txt"})
        show(
            "读目录（is_file 为假）",
            "data.readText",
            {"workspace": str(WORKSPACE), "path": "out"},
        )

    if want("data.fileExists"):
        show("存在", "data.fileExists", {"workspace": str(WORKSPACE), "path": "note.txt"})
        show("不存在", "data.fileExists", {"workspace": str(WORKSPACE), "path": "nope.txt"})
        show(
            "目录也算存在",
            "data.fileExists",
            {"workspace": str(WORKSPACE), "path": "out"},
        )

    if want("data.deletePath"):
        victim = WORKSPACE / "victim.txt"
        victim.write_text("bye", encoding="utf-8", newline="\n")
        show("删文件", "data.deletePath", {"workspace": str(WORKSPACE), "path": "victim.txt"})
        tree = WORKSPACE / "tree"
        (tree / "inner").mkdir(parents=True, exist_ok=True)
        (tree / "inner" / "x.txt").write_text("x", encoding="utf-8", newline="\n")
        show(
            "非空目录 recursive=false",
            "data.deletePath",
            {"workspace": str(WORKSPACE), "path": "tree", "recursive": False},
        )
        show(
            "非空目录 recursive=true",
            "data.deletePath",
            {"workspace": str(WORKSPACE), "path": "tree", "recursive": True},
        )
        show("删不存在的路径", "data.deletePath", {"workspace": str(WORKSPACE), "path": "ghost"})
        show(
            "逃逸工作区",
            "data.deletePath",
            {"workspace": str(WORKSPACE), "path": str(ROOT_TMP / "escape.txt")},
        )

    if want("data.writeJson"):
        show(
            "写对象",
            "data.writeJson",
            {"workspace": str(WORKSPACE), "path": "out/d.json", "data": {"b": 1, "a": [1, 2]}},
            files=[WORKSPACE / "out" / "d.json"],
        )
        show(
            "写标量",
            "data.writeJson",
            {"workspace": str(WORKSPACE), "path": "out/e.json", "data": "plain"},
            files=[WORKSPACE / "out" / "e.json"],
        )
        show(
            "data 为 null",
            "data.writeJson",
            {"workspace": str(WORKSPACE), "path": "out/f.json", "data": None},
            files=[WORKSPACE / "out" / "f.json"],
        )
        show(
            "逃逸工作区",
            "data.writeJson",
            {"workspace": str(WORKSPACE), "path": str(ROOT_TMP / "e2.json"), "data": {}},
        )

    if want("data.format"):
        show(
            "替换与 JSON 序列化",
            "data.format",
            {"template": "值={n} 列表={items} 文本={s}", "values": {"n": 3, "items": [1, 2], "s": "x"}},
        )
        show(
            "重复占位符",
            "data.format",
            {"template": "{a}-{a}-{a}", "values": {"a": "重复"}},
        )
        show(
            "无占位符",
            "data.format",
            {"template": "原样输出", "values": {"unused": 1}},
        )
        show(
            "占位符缺失",
            "data.format",
            {"template": "{a}+{b}", "values": {"a": 1}},
        )
        show(
            "values 有嵌套对象",
            "data.format",
            {"template": "{obj}", "values": {"obj": {"k": "v"}}},
        )

    if want("data.limit"):
        show("取前 2", "data.limit", {"items": [1, 2, 3, 4], "count": 2})
        show("count 超过长度", "data.limit", {"items": [1, 2], "count": 5})
        show("count=0（下界）", "data.limit", {"items": [1, 2], "count": 0})
        show("空数组", "data.limit", {"items": [], "count": 3})

    if want("data.log"):
        show("缺省 level", "data.log", {"message": "普通日志"})
        show("level=warn", "data.log", {"message": "警告", "level": "warn"})
        show("level=error", "data.log", {"message": "错误", "level": "error"})

    if want("data.setVar"):
        show("缺省 varType=string", "data.setVar", {"varName": "v1", "value": 12})
        show("string 空值", "data.setVar", {"varName": "v2", "value": None, "varType": "string"})
        show("number 从字符串", "data.setVar", {"varName": "v3", "value": " 3.5 ", "varType": "number"})
        show("number 从布尔", "data.setVar", {"varName": "v4", "value": True, "varType": "number"})
        show("number 不可转", "data.setVar", {"varName": "v5", "value": "abc", "varType": "number"})
        show("boolean 从字符串 yes", "data.setVar", {"varName": "v6", "value": "yes", "varType": "boolean"})
        show("boolean 非真串", "data.setVar", {"varName": "v7", "value": "no", "varType": "boolean"})
        show("object 从 JSON 串", "data.setVar", {"varName": "v8", "value": "{\"k\": 1}", "varType": "object"})
        show("object 从数组串（应失败）", "data.setVar", {"varName": "v9", "value": "[1]", "varType": "object"})
        show("array 从 JSON 串", "data.setVar", {"varName": "v10", "value": "[1, \"a\"]", "varType": "array"})
        show("array 空串", "data.setVar", {"varName": "v11", "value": "", "varType": "array"})

    if want("data.datetimeNow"):
        show("缺省（ISO）", "data.datetimeNow", {})
        show("自定义 format", "data.datetimeNow", {"format": "%Y-%m-%d"})
        show("format 只到年", "data.datetimeNow", {"format": "%Y"})

    if want("workflow.sleep"):
        show("零秒", "workflow.sleep", {"seconds": 0})
        show("0.05 秒", "workflow.sleep", {"seconds": 0.05})
        show("负秒（下界外）", "workflow.sleep", {"seconds": -1})

    # -- 表格命令：按真实流程顺序跑，每步 dump 表文件 -------------------------
    table_file = FLOW_DIR / "data" / "table.json"
    csv_file = FLOW_DIR / "data" / "table.csv"
    if wanted is None or any(command.startswith("data.table.") for command in wanted):
        show(
            "空表 appendRow",
            "data.table.appendRow",
            {"row": {"姓名": "张三", "年龄": 18}},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "第二行（新列自动追加到 schema）",
            "data.table.appendRow",
            {"row": {"姓名": "李四", "城市": "上海"}},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "appendRow row 不是对象",
            "data.table.appendRow",
            {"row": "不是对象"},
            flow_dir=FLOW_DIR,
        )
        show(
            "setCell 落在第 2 行",
            "data.table.setCell",
            {"row": 2, "column": "年龄", "value": 30},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "setCell 越过末行（自动补空行）",
            "data.table.setCell",
            {"row": 4, "column": "备注", "value": "补"},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "setCell row=0（下界外）",
            "data.table.setCell",
            {"row": 0, "column": "x", "value": 1},
            flow_dir=FLOW_DIR,
        )
        show(
            "getCell 命中",
            "data.table.getCell",
            {"row": 1, "column": "姓名", "varName": "who"},
            flow_dir=FLOW_DIR,
        )
        show(
            "getCell 越界（返回 None 而不是报错）",
            "data.table.getCell",
            {"row": 99, "column": "姓名", "varName": "who"},
            flow_dir=FLOW_DIR,
        )
        show(
            "getCell 列不存在",
            "data.table.getCell",
            {"row": 1, "column": "没有这列", "varName": "who"},
            flow_dir=FLOW_DIR,
        )
        show(
            "getCell row 非整数",
            "data.table.getCell",
            {"row": "x", "column": "姓名", "varName": "who"},
            flow_dir=FLOW_DIR,
        )
        show(
            "exportCsv 缺省路径（表同名 .csv）",
            "data.table.exportCsv",
            {},
            flow_dir=FLOW_DIR,
            files=[csv_file],
        )
        show(
            "exportCsv 指定相对路径",
            "data.table.exportCsv",
            {"path": "out/导出.csv"},
            flow_dir=FLOW_DIR,
            files=[FLOW_DIR / "out" / "导出.csv"],
        )
        show(
            "exportCsv 逃逸流程目录",
            "data.table.exportCsv",
            {"path": "../escape.csv"},
            flow_dir=FLOW_DIR,
        )
        show(
            "table 名非法（路径穿越）",
            "data.table.clear",
            {"table": "../evil"},
            flow_dir=FLOW_DIR,
        )
        show(
            "deleteRow 越界",
            "data.table.deleteRow",
            {"row": 99},
            flow_dir=FLOW_DIR,
        )
        show(
            "deleteRow 第 1 行",
            "data.table.deleteRow",
            {"row": 1},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "clear",
            "data.table.clear",
            {},
            flow_dir=FLOW_DIR,
            files=[table_file],
        )
        show(
            "缺 flowDir（运行时注入缺失）",
            "data.table.clear",
            {},
        )
        show(
            "多表：table=other",
            "data.table.appendRow",
            {"table": "other", "row": {"k": "v"}},
            flow_dir=FLOW_DIR,
            files=[FLOW_DIR / "data" / "other.json"],
        )

    print(f"\n探针结束；临时目录 {ROOT_TMP}（自行清理）")
    shutil.rmtree(ROOT_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
