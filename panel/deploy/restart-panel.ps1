<#
    Restart of the panel client, by hand, without reinstalling.

    .\deploy\restart-panel.ps1
    or double-click deploy\restart-panel.cmd

    What it does:
      1. stops the scheduled task
      2. waits for the pythonw process to DIE, and kills it if it does not
      3. starts the task again
      4. waits for "first frame after opening" in the log, once per screen

    Notes:
      * Kept in PURE ASCII, same reason as install-task.ps1 (PowerShell 5.1
        reads a file without a BOM as ANSI).
      * The process is found by command line, not by name -- pythonw.exe runs
        other things too. Same filter as install-task.ps1.
      * Waiting for the process, not the task state: the scheduler says "Ready"
        while pythonw still holds panel.lock, and the new instance would back
        off on the lock and leave the screen dark.
      * The screen count comes from panel.json. At restart that file exists,
        unlike at the first installation.
#>
[CmdletBinding()]
param(
    [string]$TaskName = "Claude Panel AX206",
    [int]$TimeoutSec = 45
)

$ErrorActionPreference = "Stop"

$AppDir  = Join-Path $env:LOCALAPPDATA "claude-usage-monitor"
$LogPath = Join-Path $AppDir "panel.log"
$CfgPath = Join-Path $AppDir "panel.json"

. (Join-Path $PSScriptRoot "panel-common.ps1")

function Get-PanelProcess {
    @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*panel-run.pyw*" })
}

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "task '$TaskName' does not exist - run deploy\install-task.ps1 first"
}

# --- how many screens should report in -----------------------------------
$Panels = 1
try {
    $cfg = Get-Content $CfgPath -Raw | ConvertFrom-Json
    if ($cfg.panels) { $Panels = @($cfg.panels).Count }
} catch {
    Write-Host "WARNING: cannot read $CfgPath - assuming 1 screen"
}

# --- which logs to watch -----------------------------------------------------
# With log_path set the client writes its frames there (a relative path is
# resolved against the task's working directory, $AppDir), but run.pyw still
# writes "panel already running" and "config error:" to the default panel.log.
# So both are watched; the same file is never read twice.
$Logs = @($LogPath)
if ($cfg -and $cfg.log_path) {
    $custom = [string]$cfg.log_path
    if (-not [System.IO.Path]::IsPathRooted($custom)) { $custom = Join-Path $AppDir $custom }
    if ([System.IO.Path]::GetFullPath($custom) -ne [System.IO.Path]::GetFullPath($LogPath)) {
        $Logs = @($custom, $LogPath)
    }
}

# --- 1. stop ---------------------------------------------------------------
Write-Host "stopping '$TaskName'"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline -and (Get-PanelProcess)) {
    Start-Sleep -Milliseconds 500
}
$left = Get-PanelProcess
if ($left) {
    # A hung client does not react to Stop-ScheduledTask. It still holds the
    # lock and the USB handle. Only the task's own pythonw (panel-run.pyw) is
    # matched here: a client started by hand (python -m panel) is not, and has
    # to be closed by hand.
    Write-Host "  process still alive after 15 s - killing $($left.Count)"
    foreach ($p in $left) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if (Get-PanelProcess) {
        throw "panel process will not die - check Task Manager (pythonw.exe, panel-run.pyw)"
    }
}

# --- 2. start --------------------------------------------------------------
$before = @(foreach ($l in $Logs) { if (Test-Path $l) { @(Get-Content $l).Count } else { 0 } })

Start-ScheduledTask -TaskName $TaskName
Write-Host -NoNewline "started, waiting for $Panels screen(s) in the log "

$w = Wait-PanelLog -LogPath $Logs -Before $before -Panels $Panels -TimeoutSec $TimeoutSec
$opened = $w.Opened; $unacked = $w.Unacked; $busy = $w.Busy; $dup = $w.Dup; $bad = $w.Bad
Write-Host ""
Write-Host ""

$reported = $w.Screens
if ($reported -gt 0) {
    if ($reported -lt $Panels) {
        Write-Host "WARNING - $reported of $Panels screens reported in:"
    } else {
        Write-Host "OK - panel is drawing ($reported of $Panels):"
    }
    foreach ($line in $opened)  { Write-Host "  $line" }
    foreach ($line in $unacked) {
        Write-Host "  $line"
        Write-Host "    ^ unacknowledged - check visually that something is showing"
    }
    if ($reported -lt $Panels) {
        # The wait may have ended on one of these; without them "N of M" has no reason.
        if ($busy) { Write-Host "  busy:   $busy" }
        if ($dup)  { Write-Host "  locked: $dup" }
        if ($bad)  { Write-Host "  config: $bad" }
    }
} elseif ($busy) {
    Write-Host "PANEL BUSY - another process has it:"
    Write-Host "  $busy"
} elseif ($dup) {
    Write-Host "ANOTHER INSTANCE holds the lock - the new one backed off:"
    Write-Host "  $dup"
} elseif ($bad) {
    Write-Host "CONFIGURATION ERROR:"
    Write-Host "  $bad"
    Write-Host "  fix $CfgPath and run this script again"
} else {
    Write-Host "NO CONFIRMATION within $TimeoutSec s. Log tail:"
    if (Test-Path $Logs[0]) { Get-Content $Logs[0] -Tail 10 | ForEach-Object { Write-Host "  $_" } }
}
Write-Host ""
foreach ($l in $Logs) { Write-Host "  log: $l" }
