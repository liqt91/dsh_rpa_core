#!/usr/bin/env bash
# F10 校验热键（长轮询版）判据的负向验证：逐处精准放宽，跑完逐字节还原并核 md5。
# 每个用例都写明「期望红在哪一条」，不照抄实际输出。
set -u
ROOT="D:/Users/Administrator/Documents/代码/rpa_core"
PY="$ROOT/.venv/Scripts/python.exe"
MAIN="$ROOT/apps/editor/gui/src/main.js"
API="$ROOT/apps/editor/api/api.py"
HTML="$ROOT/apps/editor/gui/index.html"
BAK_MAIN=$(mktemp); BAK_API=$(mktemp); BAK_HTML=$(mktemp)
cp "$MAIN" "$BAK_MAIN"; cp "$API" "$BAK_API"; cp "$HTML" "$BAK_HTML"
MD5_MAIN=$(md5sum "$MAIN" | cut -d' ' -f1)
MD5_API=$(md5sum "$API" | cut -d' ' -f1)
MD5_HTML=$(md5sum "$HTML" | cut -d' ' -f1)
restore() { cp "$BAK_MAIN" "$MAIN"; cp "$BAK_API" "$API"; cp "$BAK_HTML" "$HTML"; }
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

fingerprint() {
  echo "$(md5sum "$MAIN" | cut -d' ' -f1)/$(md5sum "$API" | cut -d' ' -f1)/$(md5sum "$HTML" | cut -d' ' -f1)"
}

PY_TEST="apps/xhs-workbench/tests/test_editor_verify_hotkey.py"

# 期望红：pytest（按文件内出现顺序，逗号分隔）
run_case() {
  local name="$1" expected="$2" file="$3" old="$4" new="$5" out rc got ok after
  inject "$file" "$old" "$new"
  "$PY" -m pytest "$PY_TEST" -q > .harness/spike/_hotkey_negative_case.txt 2>&1
  rc=$?
  out=$(cat .harness/spike/_hotkey_negative_case.txt)
  restore
  after="$(fingerprint)"
  got=$(printf '%s\n' "$out" | grep '^FAILED ' | sed 's/^FAILED //; s/ .*$//' | tr '\n' ',')
  ok=1
  [ "$rc" -ne 0 ] || ok=0
  [ "$got" = "$expected" ] || ok=0
  [ "$after" = "$MD5_MAIN/$MD5_API/$MD5_HTML" ] || ok=0
  if [ "$ok" -eq 1 ]; then
    printf '[PASS] %s  期望=%s 实际=%s\n' "$name" "$expected" "$got"
  else
    printf '[FAIL] %s  期望=%s 实际=%s rc=%s fp=%s\n' "$name" "$expected" "$got" "$rc" "$after"
    printf '%s\n' "$out" | tail -25
  fi
}

# 期望红：UI 门禁里哪几条
run_gate_case() {
  local name="$1" expected="$2" file="$3" old="$4" new="$5" out rc got ok after
  inject "$file" "$old" "$new"
  node scripts/check_element_library.mjs > .harness/spike/_hotkey_negative_gate.txt 2>&1
  rc=$?
  out=$(cat .harness/spike/_hotkey_negative_gate.txt)
  restore
  after="$(fingerprint)"
  got=$(printf '%s\n' "$out" | grep '^FAIL F10' | sed 's/^FAIL //; s/ — .*$//' | tr '\n' ',')
  ok=1
  [ "$rc" -ne 0 ] || ok=0
  [ "$got" = "$expected" ] || ok=0
  [ "$after" = "$MD5_MAIN/$MD5_API/$MD5_HTML" ] || ok=0
  if [ "$ok" -eq 1 ]; then
    printf '[PASS] %s  期望=%s 实际=%s\n' "$name" "$expected" "$got"
  else
    printf '[FAIL] %s  期望=%s 实际=%s rc=%s fp=%s\n' "$name" "$expected" "$got" "$rc" "$after"
    printf '%s\n' "$out" | tail -20
  fi
}

T_RISING="${PY_TEST}::test_hotkey_session_counts_rising_edges_and_stops"
T_AWAIT="${PY_TEST}::test_await_hotkey_blocks_until_edge_or_timeout"
T_WIRE="${PY_TEST}::test_dialog_wires_global_hotkey"
G_LONG='F10 全局热键：弹窗打开即挂长轮询，一次按键只校验一次，关窗停监听'
G_KEY='F10 第三条路：PPX 窗口有焦点时窗口键盘也触发校验'

# 1. 后端上升沿退化成「按住就计次」⇒ 上升沿用例必红
run_case '按住不放重复计次' "$T_RISING," \
  "$API" '        if now and not was:' '        if now:'

# 2. 边沿只 +1 不唤醒 ⇒ 挂着的长轮询要等满预算才回（判据就是「当场醒」）
run_case '边沿不唤醒长轮询' "$T_AWAIT," \
  "$API" '                    _VERIFY_HOTKEY["fired"] += 1
                    _VERIFY_HOTKEY_COND.notify_all()' '                    _VERIFY_HOTKEY["fired"] += 1'

# 3. 弹窗不武装 ⇒ 接线用例必红
run_case '弹窗不武装热键' "$T_WIRE," \
  "$MAIN" "  if (options.mode !== 'detail') armVerifyHotkey()" '  // hotkey disarmed'

# 4. 触发后不重新挂等待 ⇒ 第二次按键就丢（门禁的「立刻重新挂上」那句）
run_gate_case '长轮询触发后不再挂' "$G_LONG," \
  "$MAIN" "      triggerHotkeyVerify('await')
      idle = 0" "      triggerHotkeyVerify('await')
      return"

# 5. 长轮询读到边沿却不校验 ⇒ 一次按键都不校验
run_gate_case '读到边沿不接火' "$G_LONG," \
  "$MAIN" "      triggerHotkeyVerify('await')" '      // fire dropped'

# 6. 去掉窗口键盘那条退路 ⇒ 第三条路用例必红
# 注意：替换文本**不能**以 `//` 开头——Git Bash 会把行首 `//` 折成 `/`，
# 于是注入的不是注释而是语法错误，整个 js 解析失败会连带全红（假红）。
run_gate_case '窗口键盘退路消失' "$G_KEY," \
  "$MAIN" "document.addEventListener('keydown', (event) => {
  if (event.key !== 'F10' || !dialogMode) return
  if (triggerHotkeyVerify('keydown')) event.preventDefault?.()
})" '/* keydown fallback removed */'

# 7. 热键状态提示消失 ⇒ 用户又回到「按了没反应也不知道为什么」
run_case '热键状态提示消失' "$T_WIRE," \
  "$HTML" '          <span id="elemHotkeyHint" class="hotkeyHint" title="F10 全局监听：鼠标悬停在目标页面上也能触发" hidden></span>
' ''

restore
trap - EXIT
if [ "$(fingerprint)" = "$MD5_MAIN/$MD5_API/$MD5_HTML" ]; then
  echo 'NEGATIVE VERIFICATION PASSED'
else
  echo 'NEGATIVE VERIFICATION FAILED: md5'
fi
