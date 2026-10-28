"""保存保真度探针：把「读入 → 写回」的差异量化出来，不靠猜。

两个问题各一组读数：

A. **文本层**：`json.loads` 后按 Qt 的编码口径（`indent=2`、`ensure_ascii=False`、末尾换行）
   重新序列化，是否与磁盘原文逐字节相同？不同 ⇒ 哪怕只改一个参数，`git diff` 也是整文件。

B. **模型层**：`Workflow.model_validate(...).root.model_dump(mode="json", by_alias=True)`
   与原文 dict 相比，多/少了哪些键？多出的默认值一旦落盘就是「用户没写过的东西被写进去」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents if (p / "src" / "rpa_core").is_dir())
sys.path.insert(0, str(REPO / "src"))

from rpa_core.model.workflow import Workflow  # noqa: E402


def qt_encode(document: dict) -> bytes:
    """Qt `save_workflow()` 的编码口径。"""
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def diff_paths(a: object, b: object, prefix: str = "") -> list[str]:
    """列出两棵 dict 的键集合与值差异（只报路径，便于定位）。"""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            here = f"{prefix}.{key}" if prefix else key
            if key not in a:
                out.append(f"+ {here}")
            elif key not in b:
                out.append(f"- {here}")
            else:
                out.extend(diff_paths(a[key], b[key], here))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"~ {prefix}[] 长度 {len(a)} -> {len(b)}")
        for index, (left, right) in enumerate(zip(a, b)):
            out.extend(diff_paths(left, right, f"{prefix}[{index}]"))
    elif a != b:
        out.append(f"~ {prefix}: {a!r} -> {b!r}")
    return out


def first_line_diff(original: bytes, encoded: bytes) -> str:
    left = original.decode("utf-8").splitlines()
    right = encoded.decode("utf-8").splitlines()
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return f"line {index + 1}: {a.strip()[:70]!r} -> {b.strip()[:70]!r}"
    if len(left) != len(right):
        return f"行数 {len(left)} -> {len(right)}（公共前缀一致）"
    return "（相同）"


def main() -> int:
    targets: list[Path] = []
    for base in (REPO / "examples", REPO / "workflows"):
        if base.is_dir():
            targets.extend(sorted(base.glob("*/workflow.json")))

    text_ok = text_bad = 0
    model_ok = model_bad = 0
    for path in targets:
        rel = path.relative_to(REPO).as_posix()
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))

        encoded = qt_encode(document)
        if encoded == raw:
            text_ok += 1
            print(f"[A ok] {rel}")
        else:
            text_bad += 1
            print(f"[A  DIFF] {rel}  {first_line_diff(raw, encoded)}")

        dumped = Workflow.model_validate(document).root.model_dump(mode="json", by_alias=True)
        changes = diff_paths(document.get("root"), dumped)
        if changes:
            model_bad += 1
            print(f"[B  DIFF] {rel}  {len(changes)} 处")
            for line in changes[:6]:
                print(f"          {line}")
        else:
            model_ok += 1
            print(f"[B ok] {rel}")

    print(f"\nA 文本层：{text_ok} 个逐字节相同 / {text_bad} 个不同")
    print(f"B 模型层：{model_ok} 个无损 / {model_bad} 个有差异")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
