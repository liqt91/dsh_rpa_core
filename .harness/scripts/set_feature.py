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
PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
