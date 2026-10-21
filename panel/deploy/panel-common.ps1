<#
    Shared by install-task.ps1 and restart-panel.ps1 (dot-sourced): waiting,
    after Start-ScheduledTask, until the log says how the panel started.

    Notes:
      * Kept in PURE ASCII, same reason as install-task.ps1 (PowerShell 5.1
        reads a file without a BOM as ANSI).
      * The match strings below are log messages written by panel/panel/link.py,
        panel/panel/app.py, panel/panel/drivers/turing_rev_a.py and
        panel/run.pyw. Renaming one of them means changing it HERE, once --
        a copy missed in one script silently turns a correct start into
        "NO CONFIRMATION".
      * Only the waiting is shared. What each script then SAYS about the result
        stays in the script: an installation and a restart give different advice.
#>

function Wait-PanelLog {
    param(
        # One log or several: their new lines are read together.
        [string[]]$LogPath,
        # Line count of each log before Start-ScheduledTask; older lines are ignored.
        [int[]]$Before,
        # How many screens must report in before the wait ends early.
        [int]$Panels,
        [int]$TimeoutSec
    )
    $r = @{ Opened = @(); Unacked = @(); Screens = 0; Busy = $null; Dup = $null; Bad = $null }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        Write-Host -NoNewline "."
        $new = @()
        for ($i = 0; $i -lt $LogPath.Count; $i++) {
            if (-not (Test-Path $LogPath[$i])) { continue }
            $lines = @(Get-Content $LogPath[$i])
            if ($lines.Count -lt $Before[$i]) {
                # The log rotated during the wait (RotatingFileHandler, log.py):
                # the baseline points past the end of a fresh file. What came
                # after the baseline is in <log>.1, and the new file from its start.
                $old = "$($LogPath[$i]).1"
                if (Test-Path $old) { $new += @(Get-Content $old | Select-Object -Skip $Before[$i]) }
                $new += $lines
            } else {
                $new += @($lines | Select-Object -Skip $Before[$i])
            }
        }
        # Wait for the FIRST FRAME, not for "opened". A handle to the device
        # does not draw anything yet, and it was the missing frame that was the symptom
        # which had everything installed a second time.
        #
        # Collect ALL the lines, not the last one: with two screens one may be
        # drawing while the other is busy -- and an "OK" after the first matching line
        # would sweep that under the rug.
        $r.Opened  = @($new | Where-Object { $_ -match "first frame after opening" })
        # The full phrase, not "not acknowledged" alone: link.py's reset line
        # ("3 frames not acknowledged") carries those words too, and would count
        # a screen that is failing as one that reported in.
        $r.Unacked = @($new | Where-Object { $_ -match "first frame sent after opening \(not acknowledged" })
        $busyLines = @($new | Where-Object { $_ -match "held by another process" })
        $r.Busy    = $busyLines | Select-Object -Last 1
        $r.Dup     = $new | Where-Object { $_ -match "panel already running" }   | Select-Object -Last 1
        $r.Bad     = $new | Where-Object { $_ -match "config error:" }           | Select-Object -Last 1
        # Count SCREENS, not lines: a screen that drops and reopens inside the
        # wait writes a second "first frame" line, and two lines from one screen
        # must not read as two screens reporting in. The tag is what link.py
        # writes before the colon ("turzx-usb 5: first frame ...").
        $tags = @(@($r.Opened) + @($r.Unacked) | ForEach-Object {
            if ($_ -match '^\S+ \S+ \S+\s+(.+?): first frame') { $Matches[1] }
        } | Sort-Object -Unique)
        $r.Screens = $tags.Count
        # A busy screen ends the wait only once every screen is accounted for --
        # reported in, or busy. Breaking on the first busy line stopped the wait
        # before the other screens had a chance to report.
        $busyTags = @($busyLines | ForEach-Object {
            if ($_ -match '^\S+ \S+ \S+\s+(.+?): ') { $Matches[1] }
        } | Where-Object { $tags -notcontains $_ } | Sort-Object -Unique)
        if (($r.Screens + $busyTags.Count) -ge $Panels -or $r.Dup -or $r.Bad) { break }
    }
    $r
}
