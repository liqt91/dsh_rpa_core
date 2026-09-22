"""一次性探针（M38 建表脚手架）：跑每条 browser 命令，打印真实下发的 op/args 与结果。

用途：用例表（`tests/commands/cases/browser.json`）里的期望值必须来自**实测**，
不是读代码猜出来的。本脚本用最小必填输入 + 默认脚本化应答跑一遍全部 browser 命令，
把 (op, args) / outputs / error 原样打印，作为建表与人工复核的依据。

不入产品代码、不进 lint 门禁（`.harness/spike/` 在 ruff exclude 里）。

用法：
    uv run python .harness/spike/probe_browser_commands.py            # 全部
    uv run python .harness/spike/probe_browser_commands.py browser.click  # 指定命令
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 探针不碰真实通道：假扩展重写了 `_targets`，这里再兜一层隔离前缀，避免任何
# 漏网路径连上维护者本人的浏览器。
import os  # noqa: E402

os.environ["RPA_EXT_ENDPOINT_PREFIX"] = "rpacore-probe_"

import rpa_core.executors.browser as browser_module  # noqa: E402
from tests.commands.harness import ScriptedExtension, run_command  # noqa: E402

# ⚠️ 危险面守卫（2026-09-22 本探针第一版实测踩到）：
# `browser.closeBrowser` 的语义是「按进程名杀掉该浏览器的**全部**进程」（M35 定案），
# `browser.navigate` 在目标浏览器离线时会**真的拉起浏览器**（`launch_browser`）。
# 第一版探针没打桩，实测**杀掉了本机 21 个真实浏览器进程**。
# 与 M36（剪贴板用例往维护者前台粘贴 "hi"）同源：打桩边界要沿**真实副作用面**划，
# 不沿「参数传递面」划。用例层的同类守卫见 `tests/commands/conftest.py`。
browser_module._list_browser_processes = lambda names: [{"pid": 4242, "name": f"{names[0]}.exe"}]
browser_module._terminate_process = lambda pid, force: None
browser_module._wait_processes_exit = lambda pids, timeout_s: True
browser_module.launch_browser = lambda *a, **k: None

# 按参数名给的「像真的」取值：比机械填 "x" 更能暴露整形分支（如 selector 必须非空、
# savePath 要能落盘）。未列出的按类型给最小值。
BY_NAME: dict[str, object] = {
    "sessionId": "s",
    "selector": "#t",
    "targetSelector": "#t2",
    "name": "k",
    "value": "v",
    "text": "hi",
    "script": "1",
    "pattern": "example",
    "url": "https://example.test/",
    "savePath": str(Path("/tmp") / "rpa-probe-shot.png"),
    "saveDir": str(Path("/tmp")),
    "files": ["/tmp/f.txt"],
    "cookies": [{"name": "k", "value": "v", "url": "https://example.test/"}],
    "args": [],
    "tabIds": [7],
}

# 单值枚举参数的取值（首个值往往与默认值相同，挑非首值才能验证「非默认也生效」）
ENUM_OVERRIDE: dict[str, object] = {
    "action": "back",
    "state": "attached",
    "position": "top",
    "infoType": "html",
    "selectBy": "label",
    "operation": "uncheck",
    "clickType": "double",
    "setWay": "innerText",
    "matchBy": "title",
    "confirmText": "text",
}

# 最小输入需要额外补充的命令（必填之外的关键字段，否则走到的是「参数缺失」分支）
EXTRA: dict[str, dict[str, object]] = {
    "browser.navigate": {"action": "goto", "url": "https://example.test/"},
    "browser.closeTabs": {"tabIds": [7]},
    "browser.scroll": {"position": "top"},
    "browser.attach": {"pattern": "example"},
}


def minimal_inputs(command_id: str) -> dict[str, object]:
    """按 manifest 的 input_schema 生成最小输入（必填字段 + 少量画像字段）。"""
    manifest_path = ROOT / "commands" / Path(*command_id.split(".")[:1]) / (
        command_id.split(".")[-1] + ".json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = manifest.get("input_schema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    inputs: dict[str, object] = {}
    for field in required:
        spec = props.get(field) or {}
        if field in ENUM_OVERRIDE:
            inputs[field] = ENUM_OVERRIDE[field]
            continue
        if field in BY_NAME:
            inputs[field] = BY_NAME[field]
            continue
        kind = spec.get("type")
        if spec.get("enum"):
            inputs[field] = spec["enum"][0]
        elif kind == "integer":
            inputs[field] = 1
        elif kind == "number":
            inputs[field] = 1
        elif kind == "boolean":
            inputs[field] = True
        elif kind == "array":
            inputs[field] = []
        else:
            inputs[field] = "x"
    inputs.update(EXTRA.get(command_id, {}))
    return inputs


def main() -> int:
    wanted = sys.argv[1:]
    manifests = sorted((ROOT / "commands" / "browser").glob("*.json"))
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        command_id = manifest["id"]
        if wanted and command_id not in wanted:
            continue
        inputs = minimal_inputs(command_id)
        ext = ScriptedExtension()
        result = run_command(ext, command_id, inputs)
        print("=" * 78)
        print(f"{command_id}   inputs={json.dumps(inputs, ensure_ascii=False)}")
        print(f"  status={result.status}  error={result.error.code if result.error else None}")
        if result.error:
            print(f"  message={result.error.message[:120]}")
            if result.error.details:
                print(f"  errorDetails={json.dumps(result.error.details, ensure_ascii=False)}")
        print(f"  calls={len(ext.calls)}")
        for call in ext.calls:
            print(f"    {call.op}  {json.dumps(call.args, ensure_ascii=False)}")
        print(f"  outputs={json.dumps(result.outputs, ensure_ascii=False)}")
        kinds = [effect.kind for effect in result.effects]
        print(f"  effects={kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
