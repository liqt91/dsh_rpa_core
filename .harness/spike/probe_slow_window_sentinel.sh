#!/usr/bin/env bash
# 慢窗守候探针（M41 S5 新增，2026-09-28）—— 用于抓「全量套件变慢 / QProcess 用例 30s 超时」
# 那个宿主级瞬态窗口：循环探测金丝雀，一旦变红，同一轮里把五层耗时一并记下来，
# 用来判断慢落在哪一层（CPU / 进程启动 / 收集 / Qt 初始化 / 测试执行）。
#
# 用法（必须在仓库根跑）：bash .harness/spike/probe_slow_window_sentinel.sh [输出文件]
#   默认输出 ./_slow_window_sentinel.txt
#
# 实测背景（2026-09-28）：25 轮全绿、未抓到窗口；`qproc` 中位 2048ms / `capture` 4406ms /
# `logic` 2371ms / `cpu` 299ms / `qtInit` 274ms。同一份代码同一命令另有
# `3.40s / 4.45s / 5.37s / 6.73s / 159.6s` 的实测分布 ⇒ **单次读数不可作结论**。
# 已否证的机制（别再重查）：沙箱删除守卫（`CODEBUDDY_TOOL_CALL_ID` A/B 无差别）、进程启动、
# 导入/收集、Qt 初始化、纯 CPU。详见 `.harness/tasks/M41-capture-affordance-and-startup.md` §5.4。
PY=.venv/Scripts/python.exe
LOG="${1:-_slow_window_sentinel.txt}"
export QT_QPA_PLATFORM=offscreen          # 探针建控件但不许弹窗抢前台

t_ms() { local S E; S=$(date +%s%N); "$@" >/dev/null 2>&1; E=$(date +%s%N); echo $(( (E-S)/1000000 )); }

: > "$LOG"
for i in $(seq 1 25); do
  TS=$(date +%H:%M:%S)

  # 金丝雀：这两条各 30s 预算，红窗内必红（整文件约 62–72s），快窗内约 2s
  S=$(date +%s%N)
  $PY -m pytest tests/contract/test_gui_command_matrix.py -o addopts= -q --tb=no \
      -k "streams_jsonl or shutdown_kills" >/dev/null 2>&1
  rc=$?
  E=$(date +%s%N); A=$(( (E-S)/1000000 ))

  # 同窗口内逐层量：GUI 判据 / 纯逻辑判据 / Qt 初始化 / 纯 CPU
  B=$(t_ms $PY -m pytest tests/contract/test_gui_capture.py -o addopts= -q)
  C=$(t_ms $PY -m pytest tests/unit/test_runtime.py -o addopts= -q)
  D=$(t_ms $PY -c "import time;t=time.perf_counter();from PySide6.QtWidgets import QApplication,QLabel;app=QApplication([]);t1=time.perf_counter()-t;t=time.perf_counter()
for _ in range(20):
    w=QLabel('x');w.resize(60,30);w.show()
print('%.0f/%.0f'%((t1)*1000,(time.perf_counter()-t)*1000))")
  E2=$(t_ms $PY -c "import time;t=time.perf_counter();s=0
for _ in range(20): s+=sum(range(500000))
print('%.0f'%((time.perf_counter()-t)*1000))")

  tag="iter=$i qproc=${A}ms rc=${rc} capture=${B}ms logic=${C}ms qtInit=${D} cpu=${E2}ms"
  if [ "$rc" -ne 0 ]; then
    echo "$TS *** RED-WINDOW *** $tag" >> "$LOG"
    echo "$TS RED-WINDOW DETECTED: $tag"
  else
    echo "$TS green $tag" >> "$LOG"
  fi
done
echo "[$(date +%H:%M:%S)] SENTINEL DONE -> $LOG"
