# 在一个 Windows Terminal 窗口里按标题选中标签(UI Automation)。
# 由 actions.focus_wt_tab 调用: powershell -File wt_tab.ps1 -hwnd <h> -want <标题子串>
# 输出: SELECTED::<标签名> / NOTFOUND::<名1>||<名2>...   退出码 0 / 1
param([long]$hwnd, [string]$want)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::TabItem)
$tabs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
$names = @()
$selected = $null
foreach ($t in $tabs) {
  $n = $t.Current.Name
  $names += $n
  if ((-not $selected) -and ($n -like ("*" + $want + "*"))) {
    $sel = $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    $sel.Select()
    $selected = $n
  }
}
# 无论成败都把全部标签名带回去 —— 调用方拿它做重名检测, 省得再逐个子进程去问
if ($selected) {
  Write-Output ("SELECTED::" + $selected)
  Write-Output ("ALL::" + ($names -join "||"))
  exit 0
}
Write-Output ("NOTFOUND::" + ($names -join "||"))
exit 1
