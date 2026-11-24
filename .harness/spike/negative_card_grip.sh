#!/usr/bin/env bash
# 卡片把手探针的负向验证：三次精准放宽，逐字节还原并核 md5。
set -u
ROOT="D:/Users/Administrator/Documents/代码/rpa_core"
GUI_DIR="$ROOT/apps/editor/gui"
MAIN="$GUI_DIR/src/main.js"
CSS="$GUI_DIR/src/style.css"
PROBE="$ROOT/.harness/spike/probe_card_grip.mjs"
PY="$ROOT/.venv/Scripts/python.exe"
BAK_MAIN=$(mktemp)
BAK_CSS=$(mktemp)
cp "$MAIN" "$BAK_MAIN"
cp "$CSS" "$BAK_CSS"
MD5_MAIN=$(md5sum "$MAIN" | cut -d' ' -f1)
MD5_CSS=$(md5sum "$CSS" | cut -d' ' -f1)

restore() {
  cp "$BAK_MAIN" "$MAIN"
  cp "$BAK_CSS" "$CSS"
}
trap restore EXIT

inject() {
  "$PY" - "$1" "$2" "$3" <<'PYCODE'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); old = sys.argv[2]; new = sys.argv[3]
t = p.read_text(encoding='utf-8')
n = t.count(old)
assert n == 1, f'anchor count={n} in {p}'
p.write_bytes(t.replace(old, new, 1).encode('utf-8'))
PYCODE
}

run_case() {
  local name="$1" expected="$2" file="$3" old="$4" new="$5" out rc got ok after
  inject "$file" "$old" "$new"
  (cd "$GUI_DIR" && node "$PROBE") > .harness/spike/_card_grip_negative_case.txt 2>&1
  rc=$?
  out=$(cat .harness/spike/_card_grip_negative_case.txt)
  restore
  after="$(md5sum "$MAIN" | cut -d' ' -f1)/$(md5sum "$CSS" | cut -d' ' -f1)"
  got=$(printf '%s\n' "$out" | grep '^\[FAIL\]' | sed 's/^\[FAIL\] //; s/  .*$//' | tr '\n' ',')
  ok=1
  [ "$rc" -ne 0 ] || ok=0
  [ "$got" = "$expected" ] || ok=0
  [ "$after" = "$MD5_MAIN/$MD5_CSS" ] || ok=0
  if [ "$ok" -eq 1 ]; then
    printf '[PASS] %s  期望=%s 实际=%s rc=%s\n' "$name" "$expected" "$got" "$rc"
  else
    printf '[FAIL] %s  期望=%s 实际=%s rc=%s md5=%s\n' "$name" "$expected" "$got" "$rc" "$after"
    printf '%s\n' "$out"
  fi
}

run_case '把手移到行尾' '把手在每张卡片最左侧,' \
  "$CSS" '  width: 20px; height: 24px; margin-left: -3px; margin-right: -2px;' \
  '  width: 20px; height: 24px; margin-left: -3px; margin-right: -2px; order: 99;'

run_case '把手缩回 12px' '把手命中框 >= 18x20,' \
  "$CSS" '  width: 20px; height: 24px; margin-left: -3px; margin-right: -2px;' \
  '  width: 12px; height: 24px; margin-left: -3px; margin-right: -2px;'

run_case '空分支文案退回点选' '空分支文案说拖入而非点选,' \
  "$MAIN" "mk('span', 'ghint', empty ? '· 空，拖入指令' : \`· \${card.steps} 步\`)" \
  "mk('span', 'ghint', empty ? '· 空，点选后添加节点' : \`· \${card.steps} 步\`)"

restore
trap - EXIT
if [ "$(md5sum "$MAIN" | cut -d' ' -f1)" = "$MD5_MAIN" ] && [ "$(md5sum "$CSS" | cut -d' ' -f1)" = "$MD5_CSS" ]; then
  echo 'NEGATIVE VERIFICATION PASSED'
else
  echo 'NEGATIVE VERIFICATION FAILED: md5'
fi
