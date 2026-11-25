<#
    Installation of the AX206 panel as a scheduled task.

    .\deploy\install-task.ps1              installs / updates
    .\deploy\install-task.ps1 -Uninstall   removes the task (leaves the venv and configuration)

    What it does:
      1. creates a venv OUTSIDE the repo (%LOCALAPPDATA%\claude-usage-monitor\panel-venv)
      2. installs from requirements.txt
      3. places three redirections next to the configuration, each pointing at the repo:
           panel-guard.pyw  the guardian, the task's own process (panel/guard.pyw)
           panel-tray.pyw   the tray icon and supervisor        (panel/tray.pyw)
           panel-run.pyw    the client that draws               (panel/run.pyw)
         The guardian keeps the supervisor alive, the supervisor keeps the client
         alive; Quit in the tray icon ends all three (panel/panel/keep.py).
      4. registers the task "Claude Panel AX206" on user logon
      5. starts it and waits until the log confirms that the panel is drawing

    Notes:
      * "Run only when user is logged on" is REQUIRED: a network drive is a drive
        mapped per session, and USB access requires an interactive session.
      * ExecutionTimeLimit MUST be zero. The default "stop after 3 days"
        would kill the panel once a week, without a trace in the log.
      * pythonw.exe, not python.exe -- otherwise a console pops up at every logon.
      * This file is kept in PURE ASCII. PowerShell 5.1 reads a file without a BOM
        as ANSI, so the U+2014 dash breaks apart into three bytes, of which 0x94 is
        the closing quote U+201D -- and the parser takes it for the end of a string
        and falls over a few dozen lines further on, in a spot with no connection
        to the cause. No dash is worth that.
      * REGISTRATION IS NOT A START. The trigger is on logon, so during an
        installation on an already logged-on session the task simply sits idle. A script
        that ended at "registered" left the screen dark without
        any hint at all -- and tempted a second run.
      * The handle to the panel is EXCLUSIVE. While another program holds it, the client
        retries every 30 s and writes about it in the log ONCE (not every second), and
        another program's image stays on the glass. From outside this looks exactly like
        a failed installation, so the script reads the log after start and names it outright.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    # The name is a leftover from the single-screen days. Renaming does NOT move the task:
    # the old one would stay registered next to the new one and two clients would fight
    # over the same display.
    [string]$TaskName = "Claude Panel AX206",
    # How many screens panel.json declares. The script does not read that file (at
    # the first installation it does not exist yet), and without this number an "OK"
    # after the first line to report in would mean "drawing one screen out of two".
    [int]$Panels = 1
)

$ErrorActionPreference = "Stop"

$PanelDir = Split-Path -Parent $PSScriptRoot
$AppDir   = Join-Path $env:LOCALAPPDATA "claude-usage-monitor"
$VenvDir  = Join-Path $AppDir "panel-venv"
$Pythonw  = Join-Path $VenvDir "Scripts\pythonw.exe"
# The task runs the guardian; the guardian starts the other two (panel/panel/keep.py
# holds the same three names).
$GuardLauncher = Join-Path $AppDir "panel-guard.pyw"

. (Join-Path $PSScriptRoot "panel-common.ps1")

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        # Unregistering does not end what the task started.
        Stop-PanelProcess -TaskName $TaskName
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed task '$TaskName'"
    } else {
        Write-Host "task '$TaskName' did not exist"
    }
    Write-Host "venv and panel.json remain in $AppDir"
    return
}

Write-Host "panel: $PanelDir"
New-Item -ItemType Directory -Force $AppDir | Out-Null

# --- 1. venv --------------------------------------------------------------
if (-not (Test-Path $Pythonw)) {
    Write-Host "creating venv in $VenvDir"
    $base = (Get-Command python).Source
    & $base -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "failed to create venv" }
}
Write-Host "installing dependencies"
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip: pip upgrade failed" }
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --quiet -r (Join-Path $PanelDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }

# --- 2. redirections ------------------------------------------------------
# The same convention as with the probe: the source stays in the repo, here sit a
# few lines that run it. Editing in the repo takes effect at once -- at the next
# start of that process, which for the client and the supervisor can be any moment
# a watcher restarts them.
function Write-Launcher([string]$Name, [string]$Target) {
    $path = Join-Path $AppDir $Name
    $body = @"
# Generated by panel/deploy/install-task.ps1 -- do not edit by hand.
# The panel lives in the repo; this is only a redirection.
import os, runpy, sys, time

SRC = r"$PanelDir"

# A network drive can be unmapped when the task fires right after logon.
waited = 0
while not os.path.isdir(SRC) and waited < 120:
    time.sleep(2)
    waited += 2

sys.path.insert(0, SRC)
runpy.run_path(os.path.join(SRC, "$Target"), run_name="__main__")
"@
    Set-Content -Path $path -Value $body -Encoding utf8
    Write-Host "redirection: $path"
}
Write-Launcher "panel-guard.pyw" "guard.pyw"
Write-Launcher "panel-tray.pyw"  "tray.pyw"
Write-Launcher "panel-run.pyw"   "run.pyw"

# --- 3. task --------------------------------------------------------------
# Stop BEFORE registering, not after: -Force on a running task leaves the old
# process alive. Without this, what was there before would keep running instead of
# what was just installed -- and silently, because the image on the panel looks the same.
#
# Whatever the task state says: a process left over from an earlier install (or one
# the scheduler no longer counts as the task's) holds panel.lock just the same, and a
# new instance would walk into the taken lock, quietly back off, and leave the OLD
# code drawing -- an installation that looks successful while changing nothing.
Write-Host "stopping the previous instance, if any"
Stop-PanelProcess -TaskName $TaskName -WaitSec 20

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$GuardLauncher`"" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"

# RestartInterval/RestartCount stay, but they are not what keeps the panel up: the
# client died of MemoryError ten times in two weeks under these very settings and
# was not restarted once (the scheduler restarts a task whose START failed, not one
# whose process ended). The guardian and the tray supervisor do that now.
# Measured with the guardian in place: its process killed by hand, the task went to
# Ready and nothing came back within 3 minutes. So the guardian itself is watched by
# nobody -- it is kept small for that reason (panel/panel/guard.py).
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 99 `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)   # 0 = no limit; see the header

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Claude limits on the USB panels: guardian, tray supervisor, client (repo: $PanelDir)" | Out-Null

# --- 4. start and confirmation --------------------------------------------
# Wait for a LINE IN THE LOG, not for "State = Running". A task can be
# running and at the same time draw nothing: a busy panel, a bad token, no
# network. "Running" would be telling an untruth then.
$LogPath  = Join-Path $AppDir "panel.log"
# The supervisor's log is watched too: "panel already running" for a lock held by
# another instance is written there now, by the supervisor, before any client starts.
$TrayLog  = Join-Path $AppDir "panel-tray.log"
$GuardLog = Join-Path $AppDir "panel-guard.log"
$Logs = @($LogPath, $TrayLog)
$before = @(foreach ($l in $Logs) { if (Test-Path $l) { @(Get-Content $l).Count } else { 0 } })

Start-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "task '$TaskName' registered and started"
Write-Host -NoNewline "waiting for confirmation from the log "

# How many screens must report in. The script does NOT read panel.json -- during
# installation that file often does not exist yet (there is a separate branch for it
# below), so the number is given by hand: .\install-task.ps1 -Panels 2
#
# 45 s, as in restart-panel.ps1: three Python starts in a row now (guardian,
# supervisor, client), each from the repo, which may sit on a network drive.
$w = Wait-PanelLog -LogPath $Logs -Before $before -Panels $Panels -TimeoutSec 45
$opened = $w.Opened; $unacked = $w.Unacked; $busy = $w.Busy; $dup = $w.Dup; $bad = $w.Bad
Write-Host ""
Write-Host ""

if ($opened.Count + $unacked.Count -gt 0) {
    $reported = $w.Screens
    if ($reported -lt $Panels) {
        Write-Host "WARNING - $reported of $Panels screens reported in:"
    } else {
        Write-Host "OK - panel is drawing ($reported of $Panels):"
    }
    foreach ($line in $opened) { Write-Host "  $line" }
    foreach ($line in $unacked) {
        # This driver acknowledges NOTHING: the line only says that the bytes
        # went out. A screen unplugged, rotated or skewed will write down
        # exactly the same, so there is nothing here to count as "drawing".
        Write-Host "  $line"
        Write-Host "    ^ unacknowledged - check visually that something is actually showing"
    }
} elseif ($busy) {
    Write-Host "PANEL BUSY - another process has it:"
    Write-Host "  $busy"
    Write-Host ""
    Write-Host "  The handle to the module is exclusive - either the panel or the other program."
    Write-Host "  Stop that program (or point it at the second module). The client keeps"
    Write-Host "  retrying every 30 s and will resume drawing ON ITS OWN once the panel frees up;"
    Write-Host "  no need to restart it or install it again."
} elseif ($dup) {
    Write-Host "PANEL WAS ALREADY RUNNING - the new instance backed off when it hit the lock:"
    Write-Host "  $dup"
    Write-Host "  This is not an error. The one that was already running keeps drawing."
} elseif ($bad) {
    Write-Host "CONFIGURATION ERROR - the panel will not start:"
    Write-Host "  $bad"
    Write-Host "  Fix $AppDir\panel.json and run: Start-ScheduledTask -TaskName '$TaskName'"
} else {
    Write-Host "NO CONFIRMATION within 45 s. Check the logs - their tails are below:"
    foreach ($l in @($LogPath, $TrayLog, $GuardLog)) {
        if (Test-Path $l) {
            Write-Host "  -- $l"
            Get-Content $l -Tail 8 | ForEach-Object { Write-Host "  $_" }
        }
    }
}

Write-Host ""
Write-Host "  logs:    $LogPath"
Write-Host "           $TrayLog   (tray supervisor)"
Write-Host "           $GuardLog  (guardian)"
Write-Host "  config:  $AppDir\panel.json"
Write-Host "  tray:    right-click the icon - Restart / Stop / Start / Quit"
Write-Host "  no tray: Set-Content $AppDir\tray.cmd stop    (or start, restart, quit)"
Write-Host "  all off: Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "  start:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  test:    python -m panel --probe   (Quit from the tray first)"
