# Copy recent Tecan log files, including files locked by FluentControl/VisionX.
# Invoked from run_tecan_bundle_setup.bat (embedded log collection) via environment variables:
#   TECAN_LOG_SRC, TECAN_LOG_DST, TECAN_LOG_PATTERNS, TECAN_LOG_MAX_AGE_DAYS,
#   TECAN_LOG_MANIFEST, TECAN_LOG_LABEL

$ErrorActionPreference = 'Stop'

function Write-ManifestLine {
    param([string]$Line)
    if ($env:TECAN_LOG_MANIFEST) {
        Add-Content -LiteralPath $env:TECAN_LOG_MANIFEST -Value $Line -Encoding UTF8
    }
    Write-Output $Line
}

function Copy-LockedFile {
    param(
        [string]$Source,
        [string]$Destination
    )
    $parent = Split-Path -LiteralPath $Destination -Parent
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    try {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force -ErrorAction Stop
        return @{ Status = 'copied'; Detail = $null }
    } catch {
        $firstError = $_.Exception.Message
        try {
            $inStream = [System.IO.File]::Open(
                $Source,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            try {
                $outStream = [System.IO.File]::Open(
                    $Destination,
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
            return @{ Status = 'locked_copy'; Detail = $firstError }
        } catch {
            return @{ Status = 'failed'; Detail = $_.Exception.Message }
        }
    }
}

function Test-NeedsCopy {
    param(
        [System.IO.FileInfo]$SourceFile,
        [string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Destination)) {
        return $true
    }
    $destInfo = Get-Item -LiteralPath $Destination
    if ($destInfo.Length -ne $SourceFile.Length) {
        return $true
    }
    if ($SourceFile.LastWriteTimeUtc -gt $destInfo.LastWriteTimeUtc.AddSeconds(2)) {
        return $true
    }
    return $false
}

$src = $env:TECAN_LOG_SRC
$dst = $env:TECAN_LOG_DST
$label = if ($env:TECAN_LOG_LABEL) { $env:TECAN_LOG_LABEL } else { 'logs' }
$maxAgeDays = 7
if ($env:TECAN_LOG_MAX_AGE_DAYS) {
    $parsed = 0
    if ([int]::TryParse($env:TECAN_LOG_MAX_AGE_DAYS, [ref]$parsed)) {
        $maxAgeDays = $parsed
    }
}

if (-not $src -or -not (Test-Path -LiteralPath $src)) {
    Write-ManifestLine "SKIP locked retry for ${label}: source missing."
    exit 0
}

$patterns = @()
if ($env:TECAN_LOG_PATTERNS) {
    $patterns = $env:TECAN_LOG_PATTERNS -split '\s+' | Where-Object { $_ }
}
if (-not $patterns) {
    $patterns = @('*')
}

$cutoff = (Get-Date).AddDays(-1 * $maxAgeDays)
$files = foreach ($pattern in $patterns) {
    Get-ChildItem -LiteralPath $src -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue
}
$files = $files | Where-Object { $_.LastWriteTime -ge $cutoff } | Sort-Object FullName -Unique

$copied = 0
$locked = 0
$failed = 0
$skipped = 0

Write-ManifestLine "Locked-file retry for ${label}: $($files.Count) recent source file(s) to verify."

foreach ($file in $files) {
    $relative = $file.FullName.Substring($src.Length).TrimStart(
        [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    )
    $target = Join-Path $dst $relative
    if (-not (Test-NeedsCopy -SourceFile $file -Destination $target)) {
        $skipped++
        continue
    }
    $result = Copy-LockedFile -Source $file.FullName -Destination $target
    switch ($result.Status) {
        'copied' {
            $copied++
            Write-ManifestLine "RETRY copied: $relative"
        }
        'locked_copy' {
            $locked++
            Write-ManifestLine "RETRY locked_copy: $relative"
        }
        default {
            $failed++
            Write-ManifestLine "RETRY failed: $relative ($($result.Detail))"
        }
    }
}

Write-ManifestLine "Locked retry summary for ${label}: copied=$copied locked_copy=$locked skipped=$skipped failed=$failed"
if ($failed -gt 0) {
    exit 2
}
exit 0
