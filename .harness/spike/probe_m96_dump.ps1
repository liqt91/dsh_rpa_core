# M96: dump the full WeChat UIA tree via RawViewWalker (read only).
# ASCII only by design: PS 5.1 reads BOM-less scripts as cp936.
# Output is written as UTF-8 so Chinese control names survive.

param(
    [string]$OutFile = "D:/Temp/m96_wechat_dump.txt",
    [long]$Hwnd = 0
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$u = [System.Windows.Automation.AutomationElement]
$root = $null
if ($Hwnd -ne 0) {
    $root = $u::FromHandle([IntPtr]$Hwnd)
} else {
    $cond = New-Object System.Windows.Automation.PropertyCondition($u::ClassNameProperty, 'Qt51514QWindowIcon')
    $root = $u::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
}
if ($null -eq $root) {
    "NO WeChat root window" | Out-File -FilePath $OutFile -Encoding utf8
    exit 2
}

$w = [System.Windows.Automation.TreeWalker]::RawViewWalker
$lines = New-Object System.Collections.ArrayList
$hits = New-Object System.Collections.ArrayList

function Walk($el, $d) {
    if ($d -gt 8) { return }
    $c = $null
    try { $c = $w.GetFirstChild($el) } catch { return }
    while ($null -ne $c) {
        try {
            $cur = $c.Current
            $r = $cur.BoundingRectangle
            $box = "{0},{1} {2}x{3}" -f [int]$r.Left, [int]$r.Top, [int]$r.Width, [int]$r.Height
            $pad = '  ' * $d
            $line = "{0}- ct={1} name=[{2}] aid=[{3}] class=[{4}] {5}" -f $pad, $cur.ControlType.ProgrammaticName, $cur.Name, $cur.AutomationId, $cur.ClassName, $box
            [void]$lines.Add($line)
            if ($cur.Name -and ($cur.Name -like '*搜索*' -or $cur.AutomationId -like '*search*')) {
                [void]$hits.Add(("HIT " + $line))
            }
            Walk $c ($d + 1)
        } catch { }
        try { $c = $w.GetNextSibling($c) } catch { break }
    }
}

Walk $root 0
$out = New-Object System.Collections.ArrayList
[void]$out.Add(("total nodes=" + $lines.Count))
[void]$out.Add("--- nodes ---")
foreach ($l in $lines) { [void]$out.Add($l) }
[void]$out.Add("--- search hits ---")
foreach ($h in $hits) { [void]$out.Add($h) }
$out | Out-File -FilePath $OutFile -Encoding utf8
"wrote $OutFile nodes=$($lines.Count) hits=$($hits.Count)"
