# register_tasks.ps1
# Creates the 3 Windows scheduled tasks that drive The Filipino Standard's
# automation pipeline. Idempotent - re-running won't duplicate tasks; existing
# TFS tasks are left alone.
#
# Run from PowerShell (no admin elevation required for user-scoped tasks):
#     powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
#
# Logon type: Interactive - tasks run when the user is logged on. This avoids
# the admin-elevation requirement that S4U/Password logon types impose. To
# upgrade to "run whether user is logged on or not" after registration:
#   * Open Task Scheduler -> right-click each TFS task -> Properties -> General
#   * Select "Run whether user is logged on or not" and enter the user password.
# (Alternative: change LogonType to S4U or Password in this script and re-run
# from an elevated PowerShell prompt.)

$ErrorActionPreference = "Stop"

# Use pythonw.exe (windowless Python) so scheduled tasks don't pop visible
# console windows when they fire. HelloNorg's tasks use the same pattern.
# stdout output is silently discarded under pythonw — file logging in /logs/
# still works fine, which is the canonical output destination for these scripts.
$PythonExe   = "C:\Python313\pythonw.exe"
$ProjectRoot = "Z:\Business Empire\The Filipino Standard"
$Username    = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

# Common settings: tolerant of battery state, retries if missed, doesn't queue duplicates
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$Principal = New-ScheduledTaskPrincipal `
    -UserId   $Username `
    -LogonType Interactive `
    -RunLevel Limited

function Register-TFSTask {
    param(
        [string] $Name,
        [string] $ScriptPath,
        [string] $ScriptArgs,
        [object] $Trigger,
        [string] $Description
    )

    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  [SKIP] '$Name' already exists. Leaving as-is."
        return
    }

    $action = New-ScheduledTaskAction `
        -Execute          $PythonExe `
        -Argument         "`"$ScriptPath`" $ScriptArgs" `
        -WorkingDirectory $ProjectRoot

    Register-ScheduledTask `
        -TaskName    $Name `
        -Description $Description `
        -Action      $action `
        -Trigger     $Trigger `
        -Principal   $Principal `
        -Settings    $Settings | Out-Null

    Write-Host "  [OK]   Registered: $Name"
}

Write-Host "Registering TFS scheduled tasks for user: $Username"
Write-Host "Python:       $PythonExe"
Write-Host "Project root: $ProjectRoot"
Write-Host ""

# -- Trigger 1: every 15 minutes starting at next round 15-min mark --------
# We anchor at midnight so the schedule lines up with 00, 15, 30, 45 of each hour
$now = Get-Date
$midnight = Get-Date -Hour 0 -Minute 0 -Second 0
$trigger15min = New-ScheduledTaskTrigger `
    -Once -At $midnight `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)  # ~10 years

# Stagger image generator by 5 minutes so the two heavyweight jobs don't fire on the same minute
$trigger15minStaggered = New-ScheduledTaskTrigger `
    -Once -At $midnight.AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# -- Trigger 1c: every 5 minutes (status poller is lighter than the others) -
# Anchored at midnight so firings land on 00, 05, 10, 15, ... of each hour.
$trigger5min = New-ScheduledTaskTrigger `
    -Once -At $midnight `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# -- Trigger 2: once daily at 11:00 PM local time --------------------------
# Local time = PHT only if the OS clock is set to Asia/Manila (UTC+8).
# If the OS is set to NZST or another zone, this will fire at 23:00 local,
# not 23:00 PHT. Check Get-TimeZone before relying on this.
$triggerDaily23 = New-ScheduledTaskTrigger -Daily -At "23:00"

# -- Register each task ----------------------------------------------------

Register-TFSTask `
    -Name        "TFS Publisher Monitor" `
    -ScriptPath  "$ProjectRoot\scripts\publisher.py" `
    -ScriptArgs  "--monitor" `
    -Trigger     $trigger15min `
    -Description "The Filipino Standard - publisher monitor. Scans ClickUp every 15 minutes for SCHEDULED tasks whose publish time has arrived, publishes via Post for Me (or dry-run logs)."

Register-TFSTask `
    -Name        "TFS Image Generator" `
    -ScriptPath  "$ProjectRoot\scripts\generate_image.py" `
    -ScriptArgs  "--pending" `
    -Trigger     $trigger15minStaggered `
    -Description "The Filipino Standard - image generator. Scans ClickUp every 15 minutes for tasks with Image Prompt populated but Image URL empty, generates 2 candidates via Gemini, writes Image URL back."

Register-TFSTask `
    -Name        "TFS Status Poller" `
    -ScriptPath  "$ProjectRoot\scripts\status_poller.py" `
    -ScriptArgs  "" `
    -Trigger     $trigger5min `
    -Description "The Filipino Standard - status poller. Every 5 minutes, scans PUBLISHED tasks with placeholder Posted URL values, queries Post for Me for the real Facebook/Instagram/Threads URLs, and overwrites the placeholders. Idempotent - skips tasks that already have real URLs."

Register-TFSTask `
    -Name        "TFS Daily Cleanup" `
    -ScriptPath  "$ProjectRoot\scripts\publisher.py" `
    -ScriptArgs  "--monitor" `
    -Trigger     $triggerDaily23 `
    -Description "The Filipino Standard - daily cleanup at 23:00 local. Same publisher --monitor pass, primarily to catch PUBLISHED tasks older than 7 days and move them to COMPLETE."

Write-Host ""
Write-Host "Done. Current TFS tasks:"
$tasks = Get-ScheduledTask -TaskName "TFS *"
foreach ($t in $tasks) {
    $info = Get-ScheduledTaskInfo $t
    Write-Host ("  {0,-25}  State={1,-8}  NextRun={2}" -f $t.TaskName, $t.State, $info.NextRunTime)
}
