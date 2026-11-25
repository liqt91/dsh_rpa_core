"""执行接线探针：把「▶ 运行」的每一条承诺逐项打勾（headless 直调 RPC）。

注意与 `run_probe.py` 的区别：那个是**启动窗口**的脚本，这个是**运行接线**的探针。

为什么不在页面里点按钮验：点按钮只能验「看起来跑了」，验不了「事件是增量的」
「停止不是故障」「启动失败没有 result」这些分不清就永远查不出来的差别。界面那层
另用截图确认（见任务单 §七·七）。

判据分五组：

启动与收口
1. `startRun` 起来的是**仓库 venv** 的解释器、`-m rpa_core.cli run`、cwd 在 run 目录下
2. 跑完 → exitCode 0、result.status=succeeded、returnValue 正确
3. `result` 是**摘要**而不是整份 RunResult（大 outputs 不能整份塞给 WebView）

事件增量
4. 事件 seq 从 1 连续递增（不跳号、不重号）
5. 逐次轮询拼接的事件序列 == 全量序列（`since` 语义正确，界面才能只追加）
6. `since` 越界 → 回空列表，不抛（前端游标算错不该炸）

证据落地
7. `artifacts/<uuid>/` 下有 events.jsonl / result.json / checkpoint.json
8. 事件里的 run_id == 目录名（早期 run_id 与真实 run_id 没串）
9. 首条 runStarted、末条 runFinished

隔离与互斥
10. 样例里 `"workspace": "."` 落在 `<run>/work` 下，**仓库根不多出 hello.txt**
11. 运行中再 `startRun` → RUN_ACTIVE，且指向同一个 run（不会起两个浏览器）
12. `cancelRun` → 进程真的死了；后续 poll 标 cancelled=True 且**不给 startupError**
    （用户点的停止不是故障，混成「启动即失败」会误导人）
13. 停止后能立刻再起一个（busy 判据没有被死进程粘住）

失败与边界
14. 编译不过的流程 → exitCode 2、无 result、startupError 带 COMPILE_FAILED + 原因
15. 该 run 的 artifacts 下没有 uuid 目录（run 根本没开始，不该留下半份证据）
16. 未知 runId → 结构化 UNKNOWN_RUN（不是抛异常变成 INTERNAL_ERROR）
17. 每个 RPC 返回值都能 `json.dumps(allow_nan=False)`（bridge 就是这么送给 JS 的）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
SANDBOX = HERE / "_sandbox"
PLAIN = SANDBOX / "plain.json"


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def write_json(path: Path, document: dict) -> Path:
    path.write_bytes(
        (json.dumps(document, ensure_ascii=False, indent=2) + "\n").replace("\r\n", "\n").encode("utf-8")
    )
    return path


RESULT_FIELDS = {
    "status",
    "workflowId",
    "runIdReal",
    "startedAt",
    "endedAt",
    "returnValue",
    "error",
    "outputs",
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


serialized = 0


def bridge_safe(payload: dict, label: str) -> None:
    """bridge 用 `json.dumps(data, allow_nan=False)`，这里同口径先验一遍。"""
    global serialized
    serialized += 1
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        check(f"{label} 可序列化给 JS", False, f"{type(exc).__name__}: {exc}")


def poll_until_settled(
    run_id: str, interval: float = 0.1, timeout: float = 60.0
) -> tuple[dict, list, int, int]:
    """按界面的方式轮询：每次只拿 since 之后的增量，拼起来要等于全量。

    返回 `(最后一次 payload, 拼起来的事件, 轮询次数, 取到增量的次数)`。
    ★ 为什么要返回**取到增量的次数**：子进程导入执行器要 0.7s 上下，这段里每轮都是
    「running 但 0 条」，只数总轮询次数会把「一次取全」和「分几次取」混成一样。
    """
    seen = 0
    polls = 0
    increments = 0
    collected: list[dict] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = A.run_poll(run_id, seen)
        polls += 1
        bridge_safe(payload, f"runPoll({seen})")
        assert payload["ok"], payload
        if payload["events"]:
            increments += 1
        collected.extend(payload["events"])
        seen = payload["total"]
        if not payload["running"]:
            return payload, collected, polls, increments
        time.sleep(interval)
    raise AssertionError(f"{run_id} 在 {timeout}s 内没收口")


def wait_for_events(run_id: str, at_least: int = 2, timeout: float = 30.0) -> dict:
    """等到**真的开始出事件**再动手：子进程导入执行器要 0.5s 上下，
    这时候 cancel 掉等于验了一个「还没开始就停」的边角，不是用户遇到的场景。"""
    seen = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = A.run_poll(run_id, seen)
        seen = payload["total"]
        if seen >= at_least or not payload["running"]:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"{run_id} 迟迟没有事件")


def fixtures() -> None:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    # 慢流程 = 4 个连续 sleep。不是「一个大 sleep」：单步只会在首尾各出一条事件，
    # 轮询永远只能取到一次增量，等于把「增量」这条判据架空了。
    write_json(
        SANDBOX / "slow.json",
        {
            "schema_version": "1.0",
            "id": "slow-probe",
            "name": "slow probe",
            "inputs": {},
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": f"nap{i}",
                        "command": "workflow.sleep",
                        "with": {"seconds": 0.6},
                    }
                    for i in range(1, 5)
                ],
            },
        },
    )
    # 编译不过：data.writeText 没声明 noSuchKey（编译期键集合判据）
    write_json(
        SANDBOX / "bad.json",
        {
            "schema_version": "1.0",
            "id": "bad-probe",
            "name": "bad probe",
            "inputs": {},
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": "w",
                        "command": "data.writeText",
                        "with": {"workspace": ".", "path": "a.txt", "text": "x", "noSuchKey": 1},
                    }
                ],
            },
        },
    )
    # `workspace: "."` 相对的是**子进程 cwd** —— 用它验证「cwd 收在 run 目录」。
    # ★ 不能用 `""`：data.writeText 的 workspace 有 minLength=1，空值在 schema 层
    #   就被拒（`'' should be non-empty`），压根走不到执行器。顺带发现
    #   `examples/api-usage` 的默认 `"workspace": ""` 因此**开箱跑不起来**（见 BACKLOG）。
    write_json(
        PLAIN,
        {
            "schema_version": "1.0",
            "id": "plain-probe",
            "name": "plain probe",
            "inputs": {},
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": "hello",
                        "command": "data.writeText",
                        "with": {"workspace": ".", "path": "hello.txt", "text": "hi"},
                    }
                ],
            },
        },
    )


def main() -> int:
    fixtures()
    if not (SANDBOX / "struct.json").is_file():
        print("缺夹具 _sandbox/struct.json —— 先跑一次 struct_probe.py")
        return 2
    stray = [REPO / "hello.txt", REPO / "a.txt"]
    polluted = [str(p.relative_to(REPO)) for p in stray if p.exists()]
    # ★ 不删：本仓的删除守卫会把探针判红，而且「基线脏」本身就该报出来
    check("基线干净：仓库根没有上次跑出来的 hello.txt/a.txt", not polluted, ", ".join(polluted))

    sys.path.insert(0, str(HERE / "app"))
    global A
    import api.api as A  # noqa: E402 - 必须在 sys.path 之后导入

    info = A.bootstrap()
    check(
        "bootstrap 暴露运行解释器与 run 根目录",
        info.get("runnerPython", "").endswith((".venv/Scripts/python.exe", ".venv\\Scripts\\python.exe"))
        or "python" in str(info.get("runnerPython")),
        f"runner={info.get('runnerPython')}",
    )

    # ---- 启动与收口 -------------------------------------------------------
    started = A.start_run(rel(SANDBOX / "struct.json"))
    bridge_safe(started, "startRun")
    check("startRun 起来的是子进程而不是进程内执行", started.get("ok") and started.get("pid", 0) > 0,
          f"runId={started.get('runId')} pid={started.get('pid')}")
    check(
        "解释器 = 仓库 venv（不是 ppx 那个）",
        "rpa_core\\.venv" in started.get("python", "") or "rpa_core/.venv" in started.get("python", ""),
        started.get("python", ""),
    )
    check("argv 走 `-m rpa_core.cli run`", started.get("argv", [])[:3] == ["-m", "rpa_core.cli", "run"],
          " ".join(started.get("argv", [])[:4]))
    check(
        "cwd 收在 run 目录下（不污染仓库根）",
        Path(started["cwd"]).parent.name == started["runId"],
        started["cwd"],
    )

    final, events, struct_polls, struct_increments = poll_until_settled(started["runId"])
    check("跑完 exitCode=0", final["exitCode"] == 0, f"exit={final['exitCode']}")
    check(
        "result.status=succeeded 且 returnValue 正确",
        (final.get("result") or {}).get("status") == "succeeded"
        and final["result"].get("returnValue") == "yes",
        f"result={json.dumps(final.get('result'), ensure_ascii=False)[:120]}",
    )
    check(
        "result 是摘要（字段固定，不带 outputs 原文）",
        set(final["result"]) == RESULT_FIELDS
        and all(set(v) == {"keys", "effects"} for v in final["result"]["outputs"].values()),
        "、".join(sorted(final["result"])),
    )

    # ---- 证据 -------------------------------------------------------------
    seqs = [e["seq"] for e in events]
    check("事件 seq 从 1 连续递增", seqs == list(range(1, len(seqs) + 1)), f"seq={seqs}")
    check("首条 runStarted、末条 runFinished", events[0]["type"] == "runStarted" and events[-1]["type"] == "runFinished",
          f"{events[0]['type']} … {events[-1]['type']}")

    real = final.get("realRunId")
    arts = Path(final["artifacts"])
    run_dir = arts / str(real)
    missing = [n for n in ("events.jsonl", "result.json", "checkpoint.json") if not (run_dir / n).is_file()]
    check("证据三件套落地", not missing, f"{run_dir.name} 缺 {missing}" if missing else str(run_dir.parent.name))
    on_disk = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    check("事件里 run_id == 目录名（早期与真实 run_id 没串）",
          all(e["run_id"] == real for e in on_disk), f"{len(on_disk)} 条")
    # ★ 不判「一次就收口」，那是不稳定的：runStarted 常在进程收口前就被轮询抓到一次，
    #   于是增量次数在 1/2 之间摆动（第一版写 `increments == 1` 就偶发假红）。
    #   改成判**确定的事**：结构节点不产生 step 事件，全流程只有首尾两条。
    check(
        "结构节点（if/forEach/try/return）不产生 step 事件，只有首尾两条",
        len(on_disk) == 2 and {e["type"] for e in on_disk} == {"runStarted", "runFinished"},
        f"{len(on_disk)} 条 · {struct_polls} 次轮询里 {struct_increments} 次有增量",
    )
    overshoot = A.run_poll(started["runId"], 9999)
    check("since 越界回空列表而不是抛", overshoot["ok"] and overshoot["events"] == [], f"{len(overshoot['events'])} 条")

    # ---- 增量（必须用多步流程：4 ms 跑完的流程只会轮询一次，判据等于白写）----
    slow = A.start_run(rel(SANDBOX / "slow.json"))
    slow_final, slow_events, slow_polls, slow_increments = poll_until_settled(slow["runId"], interval=0.08)
    slow_disk = [
        json.loads(line)
        for line in (Path(slow_final["artifacts"]) / str(slow_final["realRunId"]) / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    check(
        "多步流程分多次取到增量（≥3 次，界面才是逐条长出来的）",
        slow_increments >= 3,
        f"{slow_polls} 次轮询里 {slow_increments} 次有增量 · {len(slow_events)} 条事件 · {len(slow_disk)} 条落盘",
    )
    check(
        "逐次拼接 == 全量，seq 不重不跳",
        [e["seq"] for e in slow_events] == [e["seq"] for e in slow_disk]
        == list(range(1, len(slow_disk) + 1)),
        f"{[e['seq'] for e in slow_events]}",
    )
    check(
        "4 个 sleep 节点各出 stepStarted/stepCompleted",
        len(slow_disk) >= 10
        and len({e["node_id"] for e in slow_disk if e["type"] == "stepCompleted"}) == 4,
        f"{len(slow_disk)} 条 · {sorted({e['node_id'] for e in slow_disk if e['node_id']})}",
    )

    # ---- cwd 隔离 ---------------------------------------------------------
    plain = A.start_run(rel(PLAIN))
    plain_final, _plain_events, _polls, _inc = poll_until_settled(plain["runId"])
    hello = Path(plain["cwd"]) / "hello.txt"
    check(
        '样例 `workspace: "."` 落在 run 的 work 目录',
        plain_final["exitCode"] == 0 and hello.is_file(),
        str(hello.relative_to(SANDBOX.parent.parent.parent)),
    )
    check("仓库根没有被写进 hello.txt / a.txt", not any(p.exists() for p in stray))

    # ---- 互斥与停止 -------------------------------------------------------
    stop_target = A.start_run(rel(SANDBOX / "slow.json"))
    # 等它真的开始出事件再停：在导入执行器那 0.5s 里停掉，验的是「还没开始就停」的边角
    wait_for_events(stop_target["runId"], at_least=3)
    busy = A.start_run(rel(SANDBOX / "slow.json"))
    check(
        "运行中再 startRun → RUN_ACTIVE 且指向同一个 run",
        busy.get("ok") is False and busy.get("code") == "RUN_ACTIVE" and busy.get("runId") == stop_target["runId"],
        f"{busy.get('code')} → {busy.get('runId')}",
    )
    during = A.run_poll(stop_target["runId"], 0)
    check("慢流程在跑时 running=True 且给了 elapsedMs",
          during["running"] and isinstance(during.get("elapsedMs"), (int, float)) and during["elapsedMs"] > 0,
          f"{during['total']} 条事件 · {during.get('elapsedMs')} ms")
    stopped = A.cancel_run(stop_target["runId"])
    check("cancelRun 真的把进程停了", stopped["ok"] and stopped["cancelled"] and stopped["exitCode"] is not None,
          f"exit={stopped['exitCode']}")
    after = A.run_poll(stop_target["runId"], 0)
    check("停止后 running=False、标了 cancelled", after["running"] is False and after["cancelled"] is True)
    check(
        "停止不会抹掉已经产生的事件（日志不能凭空消失）",
        after["total"] >= 3 and after["total"] == during["total"],
        f"停前 {during['total']} 条 → 停后 {after['total']} 条",
    )
    check(
        "停止**不**报 startupError（用户停的不是故障）",
        "startupError" not in after,
        json.dumps(after.get("startupError"), ensure_ascii=False)[:100],
    )
    again = A.start_run(rel(SANDBOX / "struct.json"))
    check("停止后能立刻再起一个", again.get("ok") is True, again.get("runId"))
    if again.get("ok"):
        poll_until_settled(again["runId"])

    # ---- 启动失败 ---------------------------------------------------------
    bad = A.start_run(rel(SANDBOX / "bad.json"))
    bad_final, bad_events, _polls, _inc = poll_until_settled(bad["runId"])
    check("编译不过 → exitCode=2 且没有 result",
          bad_final["exitCode"] == 2 and bad_final.get("result") is None, f"exit={bad_final['exitCode']}")
    startup = bad_final.get("startupError") or {}
    check(
        "startupError 带 COMPILE_FAILED 与原因",
        startup.get("code") == "COMPILE_FAILED" and "Unknown parameter" in str(startup.get("message")),
        str(startup.get("message"))[:90],
    )
    check("启动失败留下一份 run 目录但没有 uuid 子目录",
          not any(p.is_dir() for p in Path(bad_final["artifacts"]).iterdir()),
          str(Path(bad_final["artifacts"]).name))
    check("编译失败发生在 runStarted 之前 ⇒ 一条事件都没有",
          bad_events == [], f"{len(bad_events)} 条")

    # ---- 边界 -------------------------------------------------------------
    unknown = A.run_poll("run-does-not-exist", 0)
    check("未知 runId → 结构化 UNKNOWN_RUN", unknown.get("ok") is False and unknown.get("code") == "UNKNOWN_RUN")

    check(f"{serialized} 次 RPC 返回都可序列化给 JS", serialized >= 15, f"共 {serialized} 次")

    print()
    if failures:
        print(f"{len(failures)} 项未通过：")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("全部通过：运行接线（子进程 / 增量事件 / 停止 / 启动失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
