"""M101 探针：校验延迟分解（只读 + 一次 flash）。

问的问题：用户实测「首次 ~2s，再次更快」的时间到底落在哪一段？
候选：① gate_for_dll 冷扫 dll（Weixin.dll 上百 MB）② 微信 provider 建树 ③ 逐窗 UIA 查询。

产出四组数字，不猜：
  A. 冷启动（清扫描缓存）整体 verify
  B. 热跑（缓存命中）整体 verify
  C. gate_for_dll 冷/热单测
  D. activate 分段（模块枚举 / 扫 dll / 读字节 / poke）

★ 不给结论数字写死期望：本探针只打印，断言在契约测试里。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.capture import accessibility as acc  # noqa: E402
from rpa_core.capture.foreground_window import visible_top_windows  # noqa: E402
from rpa_core.capture.verification import verify_element  # noqa: E402

ELEMENT = ROOT / "examples" / "api-usage" / "elements" / "微信搜索框.json"


def ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def qt_window() -> tuple[int, str, str] | None:
    for w in visible_top_windows():
        if w.class_name.startswith("Qt"):
            print(f"  qt window hwnd={w.hwnd} cls={w.class_name} title={w.title!r} proc={w.process}")
            return w.hwnd, w.class_name, w.process
    return None


def main() -> int:
    print(f"python={sys.version.split()[0]}")
    hit = qt_window()
    print(f"qt_window={hit}")
    if hit is None:
        print("[跳过] 没有 Qt 壳窗口（微信没开？）")
        return 0
    hwnd, cls, proc = hit

    # ---- C: gate_for_dll 冷/热 -------------------------------------------
    found = acc.module_base_and_path(acc._pid_of_window(hwnd), "Weixin.dll")
    print(f"module_base_and_path -> {found}")
    if found is None:
        print("[跳过] 目标进程里没有 Weixin.dll")
        return 0
    _base, dll = found
    size_mb = round(Path(dll).stat().st_size / 1024 / 1024, 1)
    print(f"dll={dll} ({size_mb} MB)")

    # 分解：读 196MB 文件 vs 纯扫描（正则 + xref）
    t = time.perf_counter()
    raw = Path(dll).read_bytes()
    read_ms = ms(t)
    t = time.perf_counter()
    _ = acc.scan_gate_rva(raw)
    scan_only_ms = ms(t)
    print(f"分解: read_bytes={read_ms}ms scan_only={scan_only_ms}ms")

    acc._SCAN_CACHE.clear()
    t = time.perf_counter()
    scan_cold = acc.gate_for_dll(dll)
    cold_ms = ms(t)
    t = time.perf_counter()
    scan_hot = acc.gate_for_dll(dll)
    hot_ms = ms(t)
    print(f"gate_for_dll  cold={cold_ms}ms hot={hot_ms}ms rva={scan_cold.rva} candidates={scan_cold.candidate_count}")

    # ---- D: activate 分段 ------------------------------------------------
    t = time.perf_counter()
    wake = acc.activate(hwnd)
    act_ms = ms(t)
    print(f"activate  total={act_ms}ms ok={wake.ok} reason={wake.reason} poked={wake.poked} before={wake.before} after={wake.after}")

    # ---- A/B: 真实校验路径 -----------------------------------------------
    doc = json.loads(ELEMENT.read_text(encoding="utf-8"))
    acc._SCAN_CACHE.clear()
    t = time.perf_counter()
    cold = verify_element(doc, mode="flash")
    print(f"verify cold(after-clear)={ms(t)}ms count={cold.count} error={cold.error} hint={cold.raw.get("hint")}")
    t = time.perf_counter()
    hot = verify_element(doc, mode="flash")
    print(f"verify hot={ms(t)}ms count={hot.count} error={hot.error} hint={hot.raw.get("hint")}")
    t = time.perf_counter()
    hot2 = verify_element(doc, mode="flash")
    print(f"verify hot#2={ms(t)}ms count={hot2.count} error={hot2.error} hint={hot2.raw.get("hint")}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
