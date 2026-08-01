param(
    [ValidateSet("Copy", "Remove")]
    [string]$Action = "",

    [string]$Source = "",
    [string]$Destination = "",
    [string]$Path = "",
    [string]$Label = "",
    [string]$HeartbeatFile = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Action)) {
    if (-not [string]::IsNullOrWhiteSpace($Path) -and [string]::IsNullOrWhiteSpace($Destination)) {
        $Action = "Remove"
    } else {
        $Action = "Copy"
    }
}

if ([string]::IsNullOrWhiteSpace($HeartbeatFile) -and -not [string]::IsNullOrWhiteSpace($env:TECAN_SETUP_HEARTBEAT)) {
    $HeartbeatFile = $env:TECAN_SETUP_HEARTBEAT
}

function Update-ProgressHeartbeat {
    param([string]$Status = "")
    if ([string]::IsNullOrWhiteSpace($HeartbeatFile)) { return }
    try {
        $parent = Split-Path -Parent $HeartbeatFile
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $payload = @(
            "updated={0}" -f (Get-Date -Format "o")
            "action=$Action"
            "label=$Label"
            "status=$Status"
            "pid=$PID"
        ) -join [Environment]::NewLine
        Set-Content -LiteralPath $HeartbeatFile -Value $payload -Encoding UTF8
    } catch { }
}

function Write-VisibleProgress {
    param(
        [int]$Current,
        [int]$Total,
        [string]$Item = "",
        [switch]$Final
    )
    $pct = 0
    if ($Total -gt 0) {
        $pct = [int][Math]::Min(100, [Math]::Floor(100.0 * $Current / $Total))
    } elseif ($Final) {
        $pct = 100
    }
    $width = 32
    $filled = [int][Math]::Floor($width * $pct / 100.0)
    if ($filled -gt $width) { $filled = $width }
    $bar = ("#" * $filled) + ("-" * ($width - $filled))
    $itemText = ""
    if (-not [string]::IsNullOrWhiteSpace($Item)) {
        $itemText = $Item
        if ($itemText.Length -gt 42) {
            $itemText = "..." + $itemText.Substring($itemText.Length - 39)
        }
        $itemText = "  " + $itemText
    }
    $line = "[{0}] {1,3}%  {2:N0}/{3:N0}{4}" -f $bar, $pct, $Current, $Total, $itemText
    if ($Final) {
        Write-Host ($line.PadRight(110))
    } else {
        Write-Host ("`r" + $line.PadRight(110)) -NoNewline
    }
    try { [Console]::Out.Flush() } catch { }
    Update-ProgressHeartbeat -Status ("{0}% {1}/{2} {3}" -f $pct, $Current, $Total, $Item)
}

function Get-FilesWithHeartbeat {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$ScanLabel = "Scanning"
    )
    $files = New-Object System.Collections.Generic.List[string]
    $scanCount = 0
    $lastBeat = Get-Date
    Write-VisibleProgress -Current 0 -Total 120 -Item ("{0}..." -f $ScanLabel)

    $dirs = New-Object System.Collections.Generic.Stack[string]
    $dirs.Push($Root)
    while ($dirs.Count -gt 0) {
        $dir = $dirs.Pop()
        try {
            foreach ($filePath in [System.IO.Directory]::EnumerateFiles($dir)) {
                $files.Add([string]$filePath) | Out-Null
                $scanCount++
                $now = Get-Date
                if (($now - $lastBeat).TotalMilliseconds -ge 150) {
                    $fakeTotal = [Math]::Max($scanCount + 40, 120)
                    Write-VisibleProgress -Current $scanCount -Total $fakeTotal -Item ("found {0:N0} files..." -f $scanCount)
                    $lastBeat = $now
                }
            }
        } catch {
            # Skip unreadable directories; keep walking.
        }
        try {
            foreach ($childDir in [System.IO.Directory]::EnumerateDirectories($dir)) {
                $dirs.Push([string]$childDir)
            }
        } catch {
            # Skip unreadable directories; keep walking.
        }
        $now = Get-Date
        if (($now - $lastBeat).TotalMilliseconds -ge 400) {
            $fakeTotal = [Math]::Max($scanCount + 40, 120)
            Write-VisibleProgress -Current $scanCount -Total $fakeTotal -Item ("walking folders... {0:N0} files" -f $scanCount)
            $lastBeat = $now
        }
    }
    Write-VisibleProgress -Current $scanCount -Total ([Math]::Max($scanCount, 1)) -Item "scan complete" -Final
    return $files
}

function Invoke-CopyTree {
    if ([string]::IsNullOrWhiteSpace($Source) -or [string]::IsNullOrWhiteSpace($Destination)) {
        throw "Copy requires -Source and -Destination."
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        Write-Host "SKIP missing source: $Source"
        exit 0
    }

    $title = if ([string]::IsNullOrWhiteSpace($Label)) { $Source } else { $Label }
    Write-Host ""
    Write-Host "==== $title ===="
    Write-Host "Source: $Source"
    Write-Host "Dest:   $Destination"
    Write-Host "Scanning files (large VisionX folders can take a minute; bar updates while scanning)..."
    try { [Console]::Out.Flush() } catch { }
    Update-ProgressHeartbeat -Status "copy-start $title"

    try {
        $files = Get-FilesWithHeartbeat -Root $Source -ScanLabel "Scanning source"
    } catch {
        Write-Host ""
        Write-Host "ERROR while scanning ${Source}: $($_.Exception.Message)"
        exit 8
    }

    $total = $files.Count
    if ($total -eq 0) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Write-Host "No files to copy."
        exit 0
    }

    Write-Host ("Copying {0:N0} files..." -f $total)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    $copied = 0
    $failed = 0
    $lastBeat = Get-Date
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd("\", "/")
    foreach ($fullName in $files) {
        $relative = $fullName.Substring($sourceRoot.Length).TrimStart("\", "/")
        $target = Join-Path $Destination $relative
        $parent = Split-Path -Parent $target
        if ($parent) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        try {
            Copy-Item -LiteralPath $fullName -Destination $target -Force -ErrorAction Stop
            $copied++
        } catch {
            try {
                $inStream = [System.IO.File]::Open(
                    $fullName,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read,
                    [System.IO.FileShare]::ReadWrite
                )
                try {
                    $outStream = [System.IO.File]::Open(
                        $target,
                        [System.IO.FileMode]::Create,
                        [System.IO.FileAccess]::Write,
                        [System.IO.FileShare]::None
                    )
                    try {
                        $inStream.CopyTo($outStream)
                        $outStream.Flush($true)
                    } finally {
                        $outStream.Dispose()
                    }
                } finally {
                    $inStream.Dispose()
                }
                $copied++
            } catch {
                $failed++
            }
        }
        $done = $copied + $failed
        if ($done -eq $total -or (((Get-Date) - $lastBeat).TotalMilliseconds -ge 150)) {
            Write-VisibleProgress -Current $done -Total $total -Item $relative
            $lastBeat = Get-Date
        }
    }

    Write-VisibleProgress -Current ($copied + $failed) -Total $total -Item "done" -Final
    Write-Host ("Copied {0:N0} file(s); failed {1:N0}." -f $copied, $failed)
    if ($failed -gt 0 -and $copied -eq 0) {
        exit 8
    }
    exit 0
}

function Invoke-RemoveTree {
    $target = $Path
    if ([string]::IsNullOrWhiteSpace($target)) { $target = $Source }
    if ([string]::IsNullOrWhiteSpace($target)) {
        throw "Remove requires -Path (or -Source)."
    }
    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host "SKIP missing path: $target"
        exit 0
    }

    $title = if ([string]::IsNullOrWhiteSpace($Label)) { "Clearing $target" } else { $Label }
    Write-Host ""
    Write-Host "==== $title ===="
    Write-Host "Removing: $target"
    Write-Host "Scanning previous copy (delete can take several minutes on large VisionX trees)..."
    try { [Console]::Out.Flush() } catch { }
    Update-ProgressHeartbeat -Status "remove-start $title"

    try {
        $files = Get-FilesWithHeartbeat -Root $target -ScanLabel "Scanning previous copy"
    } catch {
        Write-Host ""
        Write-Host "ERROR while scanning ${target}: $($_.Exception.Message)"
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        exit 0
    }

    $total = $files.Count
    if ($total -eq 0) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Nothing to delete."
        exit 0
    }

    Write-Host ("Deleting {0:N0} files..." -f $total)
    $deleted = 0
    $failed = 0
    $lastBeat = Get-Date
    $ordered = $files | Sort-Object { $_.Length } -Descending
    foreach ($fullName in $ordered) {
        try {
            [System.IO.File]::SetAttributes($fullName, [System.IO.FileAttributes]::Normal)
            [System.IO.File]::Delete($fullName)
            $deleted++
        } catch {
            try {
                Remove-Item -LiteralPath $fullName -Force -ErrorAction Stop
                $deleted++
            } catch {
                $failed++
            }
        }
        $done = $deleted + $failed
        if ($done -eq $total -or (((Get-Date) - $lastBeat).TotalMilliseconds -ge 150)) {
            $leaf = Split-Path -Leaf $fullName
            Write-VisibleProgress -Current $done -Total $total -Item $leaf
            $lastBeat = Get-Date
        }
    }
    Write-VisibleProgress -Current ($deleted + $failed) -Total $total -Item "files deleted" -Final

    Write-Host "Removing empty directories..."
    try {
        Get-ChildItem -LiteralPath $target -Recurse -Directory -Force -ErrorAction SilentlyContinue |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object {
                try { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue } catch { }
            }
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    } catch { }

    Write-Host ("Deleted {0:N0} file(s); failed {1:N0}." -f $deleted, $failed)
    exit 0
}

if ($Action -eq "Remove") {
    Invoke-RemoveTree
} else {
    Invoke-CopyTree
}
