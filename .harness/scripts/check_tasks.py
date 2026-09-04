import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / ".harness"
VALID_STATUSES = {"planned", "active", "blocked", "done"}
STATUS_LINE = re.compile(r"^(?:Status:|状态：)\s*`([^`]+)`$", re.MULTILINE)


def main() -> int:
    errors: list[str] = []
    state = json.loads((HARNESS / "project_state.json").read_text(encoding="utf-8"))
    features = json.loads((HARNESS / "feature_list.json").read_text(encoding="utf-8"))
    feature_by_id = {feature["id"]: feature for feature in features["features"]}

    task_index = ROOT / state["task_index"]
    if not task_index.is_file():
        errors.append(f"missing task index: {task_index}")

    # 任务间隔期允许无 active（active_plan/active_feature 为 None）
    active_plan = state.get("active_plan")
    if active_plan is not None:
        active_plan = ROOT / active_plan
        if not active_plan.is_file():
            errors.append(f"missing active plan: {active_plan}")

    active_feature_id = state.get("active_feature")
    if active_feature_id is not None:
        active_feature = feature_by_id.get(active_feature_id)
        if active_feature is None:
            errors.append(f"unknown active feature: {active_feature_id}")
        elif active_feature["passes"]:
            errors.append(f"active feature already passes: {active_feature_id}")

    active_documents = []
    for path in sorted((HARNESS / "tasks").glob("M*.md")):
        match = STATUS_LINE.search(path.read_text(encoding="utf-8"))
        if match is None:
            errors.append(f"missing task status: {path}")
            continue
        status = match.group(1)
        if status not in VALID_STATUSES:
            errors.append(f"invalid task status {status!r}: {path}")
        if status == "active":
            active_documents.append(path.resolve())

    expected_active = [active_plan.resolve()] if active_plan is not None else []
    if active_documents != expected_active:
        errors.append(
            f"active task mismatch: expected exactly {active_plan}, found {active_documents}"
        )

    if errors:
        print("TASK CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1
    print(f"TASK CHECK PASSED ({len(feature_by_id)} features, {len(active_documents)} active task)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
