"""把 `tests/commands/cases/ocr.json` 真的跑一遍（只读探针，不改仓库文件）。

**为什么需要它**：这张表进不了默认门禁的执行层（`tests/commands/test_data_matrix.py`
的 NAMESPACES 不含 ocr，且那里的执行器是 PythonWorkerExecutor），所以表里写的
「真跑也成立」如果不实测，就只是一句自述。本探针用**同一个** `ocr_worker.handle`
（与执行器子进程里跑的是同一份代码）逐变体执行并比对 `expect`。

口径（尽量与执行层的 expect 语义对齐，但不假装等同）：
- `errorCode`：响应必须 `ok is False` 且 `code` 相等；
- `outputKeys`：`set(outputs)` 与声明集合相等；
- `outputs`：声明里的每个键值必须相等（子集匹配，不要求全等）。
`{tmp}` 占位符替换成一个**真实存在但里面没有该文件**的临时目录——这样「路径不存在」
那几条验的是 file-not-found 分支，而不是别的什么。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.workers import ocr_worker  # noqa: E402

TABLE = ROOT / "tests" / "commands" / "cases" / "ocr.json"


def _substitute(value, tmp: str):
    if isinstance(value, str):
        return value.replace("{tmp}", tmp)
    if isinstance(value, dict):
        return {k: _substitute(v, tmp) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, tmp) for v in value]
    return value


def _check(expect: dict, response: dict) -> list[str]:
    problems: list[str] = []
    if "errorCode" in expect:
        if response.get("ok") is not False:
            problems.append(f"期望失败({expect['errorCode']})，却成功了：{response}")
        elif response.get("code") != expect["errorCode"]:
            problems.append(f"错误码 {response.get('code')!r} != 期望 {expect['errorCode']!r}")
        return problems
    if response.get("ok") is not True:
        problems.append(f"期望成功，却失败：{response}")
        return problems
    outputs = response.get("outputs") or {}
    if "outputKeys" in expect and set(outputs) != set(expect["outputKeys"]):
        problems.append(f"输出键 {sorted(outputs)} != 期望 {sorted(expect['outputKeys'])}")
    for key, value in (expect.get("outputs") or {}).items():
        if outputs.get(key) != value:
            problems.append(f"输出 {key}={outputs.get(key)!r} != 期望 {value!r}")
    return problems


def main() -> int:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    total = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for command_id, spec in table.items():
            if command_id == "_notes":
                continue
            print(f"\n## {command_id}")
            for variant in spec["variants"]:
                total += 1
                inputs = _substitute(variant.get("inputs") or {}, tmp)
                response = ocr_worker.handle(
                    {"id": total, "commandId": command_id, "inputs": inputs}
                )
                problems = _check(variant.get("expect") or {}, response)
                mark = "PASS" if not problems else "FAIL"
                if problems:
                    failed += 1
                brief = response.get("code") if response.get("ok") is False else response.get("outputs")
                print(f"  [{mark}] {variant['name']}\n         实际 -> {brief}")
                for problem in problems:
                    print(f"         ✗ {problem}")
    print(f"\n合计 {total} 变体，{total - failed} PASS / {failed} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
