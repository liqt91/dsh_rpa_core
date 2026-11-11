"""采集终态落库：批量路径（``run_collection``）与编辑器路径（``editor_run``）共用。

收敛到这里的原因：两条路径的「跑完之后怎么落库」必须是**同一份**——快照、告警、
监控状态机的迁移任何一边改了另一边都得跟，放两个文件就是两处事实源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.alerts import detect_for_keyword
from src.snapshots import snapshot_from_outcome


def archive_screenshot(artifacts: Path, run_dir: Path) -> None:
    """把本次 run 的页面截图归到该 run 自己的目录下。

    流程里 ``shotResults`` 写的是 ``<artifacts>/results.png``（工作流拿不到 run_id，只能
    写固定路径），每跑一次就被覆盖。归档一份到 ``<artifacts>/<run_id>/results.png``，
    历史 run 的现场图才不会被后一次覆盖掉——定位问题时看的就是这个。
    """
    src = artifacts / "results.png"
    dst = run_dir / "results.png"
    try:
        if src.is_file() and src.parent != dst.parent:
            dst.write_bytes(src.read_bytes())
    except OSError:
        # 截图只是诊断附件，归档失败不该让一次成功的采集变成失败
        return


def record_outcome(ctx: Any, keyword: str, outcome: Any) -> dict[str, Any]:
    """终态 → 落库 + 监控状态迁移，返回给 HTTP 层的结果字典。"""
    run_id = outcome.run_id or "(unknown)"
    if outcome.group == "ok":
        snap = snapshot_from_outcome(ctx.store, keyword, outcome)
        added = ctx.alerts.record(detect_for_keyword(ctx.store, keyword))
        ctx.monitor.set_ok(keyword, run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "snapshot_items": snap.count,
            "skipped": snap.skipped,
            "alerts_added": added,
        }
    if outcome.group == "paused":
        ctx.monitor.run_paused(keyword)
        return {"ok": False, "paused": True, "run_id": run_id, "status": outcome.status}
    err = outcome.error or outcome.status
    ctx.monitor.set_failed(keyword, str(err))
    return {
        "ok": False,
        "run_id": run_id,
        "status": outcome.status,
        "group": outcome.group,
        "error": str(err),
    }
