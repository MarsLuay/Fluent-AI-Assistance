param(
    [Parameter(Mandatory = $true)][string]$HeartbeatFile,
    [Parameter(Mandatory = $true)][string]$ErrorFile,
    [Parameter(Mandatory = $true)][string]$CompleteFile,
    [Parameter(Mandatory = $true)][string]$PidFile,
    [string]$LogFile = "",
    [string]$Phase = "unknown",
    [int]$StallSeconds = 120,
    [int]$StartupGraceSeconds = 45,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Continue"
$startedAt = Get-Date
$script:wroteStall = $false

try {
    Set-Content -LiteralPath $PidFile -Value $PID -Encoding ASCII
} catch { }

function Write-StallLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "o"), $Message
    if (-not [string]::IsNullOrWhiteSpace($LogFile)) {
        try { Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 } catch { }
    }
}

function Write-StallError([string]$Reason, [string]$Detail) {
    if ($script:wroteStall) { return }
    $script:wroteStall = $true
    $hbAge = "n/a"
    $hbText = "(missing)"
    if (Test-Path -LiteralPath $HeartbeatFile) {
        try {
            $item = Get-Item -LiteralPath $HeartbeatFile
            $hbAge = [int]((Get-Date) - $item.LastWriteTime).TotalSeconds
            $hbText = (Get-Content -LiteralPath $HeartbeatFile -Raw -ErrorAction SilentlyContinue)
        } catch { }
    }
    $body = @(
        "tecan_bundle_setup STALL DETECTED"
        "time_utc: $((Get-Date).ToUniversalTime().ToString('o'))"
        "phase: $Phase"
        "reason: $Reason"
        "detail: $Detail"
        "stall_seconds_threshold: $StallSeconds"
        "startup_grace_seconds: $StartupGraceSeconds"
        "watchdog_started: $($startedAt.ToString('o'))"
        "heartbeat_age_seconds: $hbAge"
        "heartbeat_file: $HeartbeatFile"
        "heartbeat_contents:"
        $hbText
        ""
        "What this means:"
        "The support BAT looked stuck (no progress heartbeat) for longer than the threshold."
        "Common causes: silent delete of a huge previous collection, antivirus scanning VisionX databases,"
        "or a hung PowerShell copy/remove with no console updates."
        ""
        "What to do:"
        "1. Leave this window open and check Task Manager for powershell.exe / robocopy using disk."
        "2. If it has been more than ~15 minutes with no disk activity, cancel (Ctrl+C) and re-run."
        "3. Re-run with the updated run_tecan_bundle_setup.bat + copy_tree_with_progress.ps1 that show delete progress."
        "4. Send this file plus temp_files\tecan_bundle_setup.log when asking for help."
    ) -join [Environment]::NewLine
    try {
        if (Test-Path -LiteralPath $ErrorFile) {
            Copy-Item -LiteralPath $ErrorFile -Destination ($ErrorFile + ".prev.txt") -Force -ErrorAction SilentlyContinue
        }
        Set-Content -LiteralPath $ErrorFile -Value $body -Encoding UTF8
    } catch { }
    Write-StallLog "STALL written to $ErrorFile :: $Reason"
}

Write-StallLog "Stall watchdog started for phase='$Phase' (stall=${StallSeconds}s, grace=${StartupGraceSeconds}s)"

while ($true) {
    if (Test-Path -LiteralPath $CompleteFile) {
        Write-StallLog "Stall watchdog complete marker seen; exiting."
        exit 0
    }

    $elapsed = ((Get-Date) - $startedAt).TotalSeconds
    $hasHeartbeat = Test-Path -LiteralPath $HeartbeatFile

    if (-not $hasHeartbeat) {
        if ($elapsed -ge $StartupGraceSeconds) {
            Write-StallError -Reason "no_progress_heartbeat" -Detail (
                "Phase '$Phase' ran for $([int]$elapsed)s without creating a progress heartbeat. " +
                "This matches the silent freeze after 'Large VisionX databases...' when a prior collection " +
                "is deleted with no progress bar."
            )
        }
    } else {
        try {
            $age = ((Get-Date) - (Get-Item -LiteralPath $HeartbeatFile).LastWriteTime).TotalSeconds
            if ($age -ge $StallSeconds) {
                Write-StallError -Reason "stale_progress_heartbeat" -Detail (
                    "Heartbeat for phase '$Phase' is $([int]$age)s old (threshold ${StallSeconds}s). " +
                    "Progress updates stopped while the phase was still marked running."
                )
            }
        } catch { }
    }

    Start-Sleep -Seconds $PollSeconds
}
