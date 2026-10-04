# Random ding player for Claude Code Stop hook.
# Picks one random .mp3/.wav from $DingDir and plays it.
# Throttle: if another ding started within $ThrottleMs, skip (prevents
# dozens of headless sessions all dinging at once).
param(
    [string]$DingDir = $(if ($env:CLAUDE_DING_DIR) { $env:CLAUDE_DING_DIR } else { Join-Path $HOME 'Music\ding' }),
    [double]$Volume = 0.6,
    [int]$ThrottleMs = 700,
    [int]$MaxMs = 3000
)

$ErrorActionPreference = 'SilentlyContinue'

# --- throttle via a shared timestamp file ---
$stamp = Join-Path $env:TEMP 'claude_ding_last.txt'
$now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
if (Test-Path $stamp) {
    $prev = 0
    [long]::TryParse((Get-Content $stamp -Raw).Trim(), [ref]$prev) | Out-Null
    if (($now - $prev) -lt $ThrottleMs) { exit 0 }
}
Set-Content -Path $stamp -Value $now -Encoding ascii

# --- pick a file ---
$files = @(Get-ChildItem -Path $DingDir -File -Include *.mp3,*.wav -Recurse)
if ($files.Count -eq 0) {
    # fallback to the old beeps if the folder is empty/missing
    [console]::beep(880,120); [console]::beep(1320,150)
    exit 0
}
$f = $files[(Get-Random -Maximum $files.Count)]

# --- play (MediaPlayer handles mp3; SoundPlayer would be wav-only) ---
Add-Type -AssemblyName presentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([uri]$f.FullName)
$n = 0
while (-not $p.NaturalDuration.HasTimeSpan -and $n -lt 100) { Start-Sleep -Milliseconds 20; $n++ }
$p.Volume = $Volume
$p.Play()
$dur = 2000
if ($p.NaturalDuration.HasTimeSpan) { $dur = $p.NaturalDuration.TimeSpan.TotalMilliseconds }
Start-Sleep -Milliseconds ([math]::Min($dur + 250, $MaxMs))
$p.Stop()
$p.Close()
