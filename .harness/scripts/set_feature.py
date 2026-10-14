import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / ".harness" / "feature_list.json"

parser = argparse.ArgumentParser()
parser.add_argument("feature_id")
parser.add_argument("status", choices=("true", "false"))
args = parser.parse_args()

data = json.loads(PATH.read_text(encoding="utf-8"))
for feature in data["features"]:
    if feature["id"] == args.feature_id:
        feature["passes"] = args.status == "true"
        break
else:
    raise SystemExit(f"Unknown feature: {args.feature_id}")
PATH.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
    # ★ `newline="\n"` **不能省**：Windows 上 `Path.write_text` 默认把 `\n` 翻译成 `\r\n`，
    # 于是改一次 `passes` 就把**整个** feature_list.json 漂成 CRLF（2026-10-14 实锤：
    # 一次调用产生 426 个 CRLF），而全仓约定是 LF。这不是格式洁癖——CRLF 会让
    # `git diff` 把整文件标成改动，把「一行 passes」的改动藏进几千行噪声里。
    # 现在由 `.harness/scripts/check_ledger.py` 盯着（台账文件 CRLF 必须为 0）。
    newline="\n",
)
