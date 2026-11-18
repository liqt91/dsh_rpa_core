"""``rpa_core.capture.accessibility`` 的门禁判据。

背景：微信 4.x 的 QAccessible gate 冷启动为 0，标准 UIA/MSAA 只能读到 Qt 外壳，
表现为「这个窗口里没有控件」。影刀（activity_weixin_v4/wechat.py）的做法是
在目标 dll 里定位 active byte、热写为 1、发 WM_GETOBJECT —— 无需重启。

这里锁四件事（都是「改坏了会静默出错」的地方）：

1. PE 节表解析：RVA/raw 偏移换算错了，gate 会写到只读段或错位地址。
2. gate 定位三层判据：指���必须在可执行段、目标必须在可写段、必须与
   ``qt.accessibility.core`` 的引用点在同一段代码里——少一层就会写到别处。
3. 「休眠」判定只对 Qt 窗口壳成立，不能把原生应用（本来就一层）也算进来。
4. ``activate`` 的 reason 必须落在封闭枚举里——调用方要据此出文案，
   冒出枚举外的字符串就等于文案没写。
"""

from __future__ import annotations

import struct

import pytest

from rpa_core.capture import accessibility as acc

# --------------------------------------------------------------------------
# 合成 PE：三个节，.text 放 gate 指令 + 指向 core 的 lea，.rdata 放字符串，
# .data 放 gate 字节。RVA/raw 布局固定，便于逐字节断言。
# --------------------------------------------------------------------------

TEXT_RVA, TEXT_RAW, SEC_SIZE = 0x1000, 0x400, 0x400
RDATA_RVA, RDATA_RAW = 0x2000, 0x800
DATA_RVA, DATA_RAW = 0x3000, 0xC00
GATE_RVA = DATA_RVA + 0x10


def _build_pe(text: bytes, rdata: bytes, data: bytes) -> bytes:
    """构造最小 PE32+ 镜像（够 ``pe_sections`` 解析即可）。"""
    opt_size = 0xF0
    nsec = 3
    headers = 0x400
    blob = bytearray(headers + SEC_SIZE * 3)
    blob[0:2] = b"MZ"
    struct.pack_into("<I", blob, 0x3C, 0x80)
    blob[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", blob, 0x84, 0x8664)  # machine = x64
    struct.pack_into("<H", blob, 0x84 + 2, nsec)
    struct.pack_into("<H", blob, 0x84 + 16, opt_size)
    sect_off = 0x80 + 4 + 20 + opt_size
    specs = [
        (b".text", TEXT_RVA, TEXT_RAW, 0x60000020),   # CODE | EXECUTE | READ
        (b".rdata", RDATA_RVA, RDATA_RAW, 0x40000040),  # INITIALIZED_DATA | READ
        (b".data", DATA_RVA, DATA_RAW, 0xC0000040),   # READ | WRITE
    ]
    for i, (name, rva, raw, chars) in enumerate(specs):
        base = sect_off + i * 40
        blob[base:base + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", blob, base + 8, SEC_SIZE, rva, SEC_SIZE, raw)
        struct.pack_into("<I", blob, base + 36, chars)
    blob[TEXT_RAW:TEXT_RAW + len(text)] = text
    blob[RDATA_RAW:RDATA_RAW + len(rdata)] = rdata
    blob[DATA_RAW:DATA_RAW + len(data)] = data
    return bytes(blob)


def _pe_with_gate(*, core_far: bool = False, gate_in_text: bool = False,
                  core_present: bool = True) -> bytes:
    """在 .text 里埋「cmp byte[rip+disp],0」并把 disp 指向 .data 的 gate 字节。"""
    text = bytearray()
    # lea rcx, [rip+disp] 指向 core 字符串（让扫描器认得出这是 Qt 二进制）
    core_rva = RDATA_RVA
    if core_far:
        core_rva = RDATA_RVA + 0x20000  # 超出阈值，应被过滤
    lea_at = 0
    text += b"\x48\x8d\x0d" + struct.pack("<i", core_rva - (TEXT_RVA + lea_at + 7))
    # cmp 指令：`48 85 C9` + `0F 84 rel32`（共 9 字节）之后才是 `80 3D disp32 imm8`
    cmp_off = len(text)
    target = TEXT_RVA + 0x40 if gate_in_text else GATE_RVA
    cmp_at = TEXT_RVA + cmp_off + 9
    disp = target - (cmp_at + 7)
    text += b"\x48\x85\xc9\x0f\x84" + b"\x00\x00\x00\x00"
    text += b"\x80\x3d" + struct.pack("<i", disp) + b"\x00\x0f\x84" + b"\x00\x00\x00\x00"
    rdata = acc.CORE_STRING if core_present else b"nothing here" + b"\0" * 8
    return _build_pe(bytes(text), rdata, b"\x00" * SEC_SIZE)


def test_pe_sections_reads_rva_and_flags():
    sections = acc.pe_sections(_pe_with_gate())
    assert [s.name for s in sections] == [".text", ".rdata", ".data"]
    text, rdata, data = sections
    assert (text.rva, text.raw_ptr) == (TEXT_RVA, TEXT_RAW)
    assert text.executable and not text.writable
    assert not rdata.executable
    assert data.writable and not data.executable


def test_pe_sections_rejects_non_pe():
    assert acc.pe_sections(b"not a pe at all") == []
    assert acc.pe_sections(b"MZ" + b"\0" * 0x100) == []


def test_rip_xrefs_report_instruction_start():
    """引用点必须报在**指令起始字节**上，REX 与非 REX 两种形式都要精确。

    ★ 只断言「扫描能找到 gate」是不够的：REX 分支与非 REX 分支算出的 disp
      偏移数学上等价（``pos+2 == rex+3``、``pos+6 == rex+7``），唯一差别就是
      返回位置差 1 字节；而 ``core_xrefs`` 只用于距离过滤，差 1 完全不影响
      gate 定位。2026-11-18 负向验证实测：把 ``has_rex`` 恒置 False（等价于
      把 REX 指令当 6 字节解析）时，旧判据全绿——**分支写反了也发现不了**。
      这里直接钉住返回值本身。
    """
    # 0..6：REX.W + lea，指令起点 0
    first = b"\x48\x8d\x0d" + struct.pack("<i", RDATA_RVA - (TEXT_RVA + 7))
    # 7..12：不带 REX 的 lea，指令起点 7
    second_at = len(first)
    second = b"\x8d\x0d" + struct.pack(
        "<i", RDATA_RVA - (TEXT_RVA + second_at + 6)
    )
    pe = _build_pe(first + second, acc.CORE_STRING, b"\x00" * SEC_SIZE)
    refs = acc.rip_xrefs_to(pe, acc.pe_sections(pe), RDATA_RVA)
    assert refs == [TEXT_RVA, TEXT_RVA + second_at], (
        "REX 形式应报前缀位置、非 REX 形式应报 0x8D 位置，且同一条指令只记一次"
    )


def test_scan_finds_gate_in_writable_section():
    scan = acc.scan_gate_rva(_pe_with_gate())
    assert scan.rva == GATE_RVA, "gate 必须落在 .data 的那个偏移上"
    assert scan.candidate_count == 1
    assert scan.core_xref_count == 1, "core 字符串的 lea 引用应被认出来"
    assert scan.distance is not None and scan.distance <= acc._MAX_CORE_DISTANCE


def test_scan_rejects_gate_target_in_executable_section():
    """disp 指向 .text（只读可执行）时必须是 0 候选 —— 否则会写崩代码段。"""
    scan = acc.scan_gate_rva(_pe_with_gate(gate_in_text=True))
    assert scan.rva is None
    assert scan.candidate_count == 0


def test_scan_requires_core_string():
    """找不到 ``qt.accessibility.core`` 时不做猜测（宁可不唤醒也不乱写）。"""
    scan = acc.scan_gate_rva(_pe_with_gate(core_present=False))
    assert scan.core_xref_count == 0
    assert scan.rva is None


def test_scan_filters_candidates_far_from_core_xref():
    """core 引用点离 gate 太远 = 不是同一段无障碍代码，必须过滤掉。"""
    scan = acc.scan_gate_rva(_pe_with_gate(core_far=True))
    assert scan.rva is None
    assert scan.candidate_count == 0


@pytest.mark.parametrize(
    ("cls", "kids", "expect"),
    [
        ("Qt51514QWindowIcon", 1, True),
        ("Qt51514QWindowIcon", 0, True),
        ("Qt51514QWindowIcon", 2, False),      # 已经能看到子节点 = 没休眠
        ("Chrome_WidgetWin_1", 1, False),      # 原生/Chromium 壳不算
        ("Notepad", 0, False),
        ("", 0, False),
    ],
)
def test_looks_dormant_only_for_qt_shell(cls, kids, expect):
    assert acc.looks_dormant(cls, kids) is expect


def test_wake_reason_vocabulary_is_closed():
    """activate 返回的 reason 必须都在枚举里——调用方按它选文案。"""
    seen = {
        "already_active", "activated", "no_window", "no_process", "no_dll",
        "no_gate", "read_failed", "write_failed",
    }
    assert set(acc.WakeResult.REASONS) == seen


# --------------------------------------------------------------------------
# activate 的分支：全部打桩，验证「读不到就不写」「写完必须读回校验」
# --------------------------------------------------------------------------

def _stub(monkeypatch, **kw):
    defaults = {
        "_pid_of_window": lambda hwnd: 4242,
        "module_base_and_path": lambda pid, name: (0x10000000, "X:/Weixin.dll"),
        "gate_for_dll": lambda path: acc.GateScan(0x1234, 1, 1, 0x20),
        "read_byte": lambda pid, addr: 0,
        "write_byte": lambda pid, addr, val: True,
        "poke_provider": lambda hwnd: True,
    }
    defaults.update(kw)
    for name, fn in defaults.items():
        monkeypatch.setattr(acc, name, fn)


def test_activate_writes_one_byte_when_gate_closed(monkeypatch):
    calls: list[tuple[int, int]] = []
    state = {"value": 0}

    def fake_write(pid, addr, val):
        calls.append((addr, val))
        state["value"] = val
        return True

    _stub(monkeypatch, read_byte=lambda pid, addr: state["value"], write_byte=fake_write)
    result = acc.activate(0x1111)
    assert result.ok and result.reason == "activated"
    assert result.before == 0 and result.after == 1
    assert calls == [(0x10001234, 1)], "必须写在 base+gate_rva、且只写一个 0x01"


def test_activate_is_idempotent_when_gate_already_one(monkeypatch):
    writes: list = []
    pokes: list = []
    _stub(monkeypatch, read_byte=lambda pid, addr: 1,
          write_byte=lambda pid, addr, val: writes.append(1) or True,
          poke_provider=lambda hwnd: pokes.append(hwnd))
    result = acc.activate(0x1111)
    assert result.reason == "already_active"
    assert writes == [], "已经是 1 还写 = 多一次无谓的内存写"
    assert pokes == [0x1111], "已开也要补发 WM_GETOBJECT（provider 可能还没建）"


def test_activate_refuses_when_read_fails(monkeypatch):
    """读不到当前值就不许写——不知道原值就无法还原。"""
    writes: list = []
    _stub(monkeypatch, read_byte=lambda pid, addr: None,
          write_byte=lambda pid, addr, val: writes.append(1) or True)
    result = acc.activate(0x1111)
    assert not result.ok and result.reason == "read_failed"
    assert writes == []


def test_activate_reports_write_failure_when_readback_differs(monkeypatch):
    """写成功但读回不是 1：必须报失败，不能当成功（否则用户对着空树等）。"""
    seq = iter([0, 0])
    _stub(monkeypatch, read_byte=lambda pid, addr: next(seq))
    result = acc.activate(0x1111)
    assert not result.ok and result.reason == "write_failed"
    assert result.after == 0


def test_activate_without_dll_is_reported(monkeypatch):
    _stub(monkeypatch, module_base_and_path=lambda pid, name: None)
    result = acc.activate(0x1111)
    assert result.reason == "no_dll"


def test_activate_without_gate_is_reported(monkeypatch):
    _stub(monkeypatch, gate_for_dll=lambda path: acc.GateScan(None, 0, 0, None))
    result = acc.activate(0x1111)
    assert result.reason == "no_gate"


def test_activate_zero_hwnd(monkeypatch):
    _stub(monkeypatch)
    assert acc.activate(0).reason == "no_window"


# --------------------------------------------------------------------------
# M100：poke 的送达结果必须被记下来（以前不看返回值，日志里恒 ok=true）
# --------------------------------------------------------------------------

def test_activate_records_poke_delivered(monkeypatch):
    seq = iter([0, 1])
    _stub(
        monkeypatch,
        read_byte=lambda pid, addr: next(seq),
        poke_provider=lambda hwnd: True,
    )
    result = acc.activate(0x1111)
    assert result.reason == "activated"
    assert result.poked is True


def test_activate_records_poke_rejected(monkeypatch):
    """微信实测 SendMessageTimeout 返回 0（err 5 拒绝访问）：gate 写成功、poke 没送达。

    两者必须分开报——否则「provider 没响应」会被读成「树建好了但没节点」。
    """
    seq = iter([0, 1])
    _stub(
        monkeypatch,
        read_byte=lambda pid, addr: next(seq),
        poke_provider=lambda hwnd: False,
    )
    result = acc.activate(0x1111)
    assert result.ok and result.reason == "activated"
    assert result.poked is False


def test_activate_poke_false_means_not_attempted(monkeypatch):
    """``poke=False``（保活路径）下 poked 是 None，不是 False——没试过 ≠ 试了失败。"""
    _stub(monkeypatch, read_byte=lambda pid, addr: 1)
    result = acc.activate(0x1111, poke=False)
    assert result.reason == "already_active"
    assert result.poked is None


def test_wake_if_dormant_ignores_native_windows(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(acc, "window_class_name", lambda hwnd: "Chrome_WidgetWin_1")
    monkeypatch.setattr(acc, "activate", lambda hwnd, **kw: calls.append(hwnd))
    assert acc.wake_if_dormant(0x1111) is None
    assert calls == [], "非 Qt 壳窗口一个字节都不该动"


def test_wake_if_dormant_activates_qt_shell(monkeypatch):
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(acc, "window_class_name", lambda hwnd: "Qt51514QWindowIcon")
    monkeypatch.setattr(
        acc, "activate",
        lambda hwnd, *, dll_name="Weixin.dll", poke=True: calls.append((hwnd, poke))
        or acc.WakeResult(True, "activated", 4242, "X:/Weixin.dll", 0x1234, 0, 1, True),
    )
    result = acc.wake_if_dormant(0x1111)
    assert result is not None and result.ok
    assert calls == [(0x1111, True)]


def test_wake_if_dormant_zero_hwnd(monkeypatch):
    monkeypatch.setattr(acc, "window_class_name", lambda hwnd: "Qt51514QWindowIcon")
    assert acc.wake_if_dormant(0) is None
