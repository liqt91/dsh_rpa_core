# M87 real-machine probe (ASCII only: PS 5.1 reads BOM-less scripts as cp936,
# so any non-ASCII here would be mangled).
#
# Question: why does WeChat's accessibility tree disappear a few seconds after
# it was woken? probe_m87_gate_flap.py showed the gate byte stays 1 for 30s when
# nobody connects as a UIA client. This probe does the *real* thing:
#   wake gate -> connect as a UIA client -> keep polling gate + descendant count.
# If the gate flips back to 0 only while a client is attached, the reset is
# driven by the client lifecycle, not by a blind timer.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File probe_m87_ps_uia.ps1 60

param([int]$Seconds = 60)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$src = @'
using System;
using System.Text;
using System.Runtime.InteropServices;

public class Nat {
    public delegate bool EnumProc(IntPtr h, IntPtr p);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int m);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);

    [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(int access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] public static extern bool ReadProcessMemory(IntPtr p, IntPtr addr, byte[] buf, IntPtr size, out IntPtr read);
    [DllImport("kernel32.dll")] public static extern bool WriteProcessMemory(IntPtr p, IntPtr addr, byte[] buf, IntPtr size, out IntPtr written);

    public static IntPtr Handle = IntPtr.Zero;
    public static uint Pid = 0;
    public static string Desc = "";

    public static void Find() {
        Handle = IntPtr.Zero;
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            if (!IsWindowVisible(h)) return true;
            StringBuilder sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            string cls = sb.ToString();
            if (!(cls.StartsWith("Qt") && cls.EndsWith("WindowIcon"))) return true;
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            string pname = "";
            try { pname = System.Diagnostics.Process.GetProcessById((int)pid).ProcessName; } catch { return true; }
            if (!pname.Equals("Weixin", StringComparison.OrdinalIgnoreCase)) return true;
            Handle = h;
            Pid = pid;
            Desc = cls + " pid=" + pid;
            return false;
        }, IntPtr.Zero);
    }

    public static int ReadByte(IntPtr proc, IntPtr addr) {
        byte[] b = new byte[1];
        IntPtr got;
        if (!ReadProcessMemory(proc, addr, b, (IntPtr)1, out got)) return -1;
        return b[0];
    }

    public static bool WriteByte(IntPtr proc, IntPtr addr, byte v) {
        byte[] b = new byte[] { v };
        IntPtr written;
        return WriteProcessMemory(proc, addr, b, (IntPtr)1, out written);
    }
}
'@
Add-Type -TypeDefinition $src

[Nat]::Find()
if ([Nat]::Handle -eq [IntPtr]::Zero) {
    Write-Output "[FATAL] no WeChat main window found"
    exit 2
}

$proc = Get-Process -Id ([Nat]::Pid)
$mod = $proc.Modules | Where-Object { $_.ModuleName -ieq 'Weixin.dll' } | Select-Object -First 1
if ($null -eq $mod) {
    Write-Output "[FATAL] Weixin.dll not loaded in pid $([Nat]::Pid)"
    exit 2
}

# RVA discovered by .harness/spike/probe_m87_gate_flap.py (QAccessible::active).
$rva = 0xB50ABC8
$base = $mod.BaseAddress.ToInt64()
$addr = [IntPtr]($base + $rva)
Write-Output ("window=0x{0:X} {1} base=0x{2:X} addr=0x{3:X}" -f [Nat]::Handle.ToInt64(), [Nat]::Desc, $base, $addr.ToInt64())

$hProc = [Nat]::OpenProcess(0x38, $false, [Nat]::Pid)   # QUERY_INFO|VM_OPERATION|VM_WRITE|VM_READ|SYNCHRONIZE-ish
if ($hProc -eq [IntPtr]::Zero) {
    Write-Output "[FATAL] OpenProcess failed; elevated token required"
    exit 2
}

$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker

function Count-Descendants([System.Windows.Automation.AutomationElement]$root) {
    $seen = 0
    $classes = @{}
    $queue = New-Object System.Collections.Queue
    $first = $walker.GetFirstChild($root)
    if ($first -ne $null) { $queue.Enqueue($first) }
    while ($queue.Count -gt 0 -and $seen -lt 400) {
        $el = $queue.Dequeue()
        $seen++
        try {
            $c = $el.Current.ClassName
            if ($c) { if ($classes.ContainsKey($c)) { $classes[$c]++ } else { $classes[$c] = 1 } }
        } catch { }
        try {
            $child = $walker.GetFirstChild($el)
            if ($child -ne $null) { $queue.Enqueue($child) }
        } catch { }
        try {
            $sib = $walker.GetNextSibling($el)
            if ($sib -ne $null) { $queue.Enqueue($sib) }
        } catch { }
    }
    return @($seen, $classes)
}

function Snapshot([int]$t) {
    $gate = [Nat]::ReadByte($hProc, $addr)
    $el = [System.Windows.Automation.AutomationElement]::FromHandle([Nat]::Handle)
    $res = Count-Descendants $el
    $n = $res[0]
    $cls = $res[1]
    $top = ($cls.GetEnumerator() | Sort-Object -Property Value -Descending | Select-Object -First 4 | ForEach-Object { "$($_.Key)x$($_.Value)" }) -join ","
    Write-Output ("t={0,3}s gate={1} desc={2} [{3}]" -f $t, $gate, $n, $top)
}

$gate0 = [Nat]::ReadByte($hProc, $addr)
Write-Output "initial gate=$gate0"
if ($gate0 -eq 0) {
    $ok = [Nat]::WriteByte($hProc, $addr, 1)
    [void][Nat]::SendMessage([Nat]::Handle, 0x003D, [IntPtr]::Zero, [IntPtr](-16))
    Write-Output "wrote 1 -> $ok"
}
Start-Sleep -Milliseconds 600

Snapshot 0
for ($t = 3; $t -le $Seconds; $t += 3) {
    Start-Sleep -Seconds 3
    Snapshot $t
}

[Nat]::CloseHandle($hProc) | Out-Null
Write-Output "done"
