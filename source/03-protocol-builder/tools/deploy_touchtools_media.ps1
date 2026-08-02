param(
    [Parameter(Mandatory = $true)]
    [string]$BundleRoot,

    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

function Wait-IfNeeded {
    if (-not $NoPause) {
        Read-Host "Press Enter to continue"
    }
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
}

function Write-DeployLog {
    param([string]$Message, [string]$LogPath)
    Write-Host $Message
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f $stamp, $Message)
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Try-CopyOne {
    param(
        [string]$SourceFile,
        [string]$DestFile
    )
    $tmp = Join-Path $env:TEMP ("tt_deploy_{0}_{1}" -f $PID, [IO.Path]::GetFileName($DestFile))
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        try {
            if (Test-Path -LiteralPath $DestFile) {
                Remove-Item -LiteralPath $DestFile -Force -ErrorAction SilentlyContinue
                if (Test-Path -LiteralPath $DestFile) {
                    $bak = $DestFile + ".deploy_bak"
                    Rename-Item -LiteralPath $DestFile -NewName ([IO.Path]::GetFileName($bak)) -Force -ErrorAction SilentlyContinue
                }
            }
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
            Copy-Item -LiteralPath $SourceFile -Destination $tmp -Force
            Move-Item -LiteralPath $tmp -Destination $DestFile -Force
            if (Test-Path -LiteralPath $DestFile) { return $true }
        } catch { }
        Start-Sleep -Seconds ([Math]::Min($attempt, 5))
    }
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    return $false
}

$root = [System.IO.Path]::GetFullPath($BundleRoot)
$logPath = Join-Path $root "deploy_touchtools_images.log"
$imagesRoot = Join-Path $env:ProgramData "Tecan\VisionX\TouchToolsData\Images"
$copied = 0
$skipped = 0
$failed = 0
$err = $false

Set-Content -LiteralPath $logPath -Value ("[{0}] deploy_touchtools_media.ps1 started" -f (Get-Date -Format "o")) -Encoding UTF8
Write-DeployLog "===================================================" $logPath
Write-DeployLog "TouchTools media deploy" $logPath
Write-DeployLog ("Bundle dir: {0}" -f $root) $logPath
Write-DeployLog ("Log file: {0}" -f $logPath) $logPath
Write-DeployLog "Tip: close FluentControl Script Editor preview windows before deploy." $logPath

$mediaSubfolder = $null
$deployCfg = Join-Path $root "source\touchtools_deploy.json"
$metadata = Join-Path $root "source\metadata.json"
if (Test-Path -LiteralPath $deployCfg) {
    $cfg = Get-Content -Raw -LiteralPath $deployCfg | ConvertFrom-Json
    if ($cfg.media_subfolder) { $mediaSubfolder = [string]$cfg.media_subfolder }
}
if ([string]::IsNullOrWhiteSpace($mediaSubfolder) -and (Test-Path -LiteralPath $metadata)) {
    $meta = Get-Content -Raw -LiteralPath $metadata | ConvertFrom-Json
    if ($meta.script_name) { $mediaSubfolder = ([string]$meta.script_name) + "_media" }
}
if ([string]::IsNullOrWhiteSpace($mediaSubfolder)) { $mediaSubfolder = "script_media" }
$dest = Join-Path $imagesRoot $mediaSubfolder

foreach ($proc in @("FluentControl.exe", "VisionX.exe")) {
    if (Get-Process -Name ($proc -replace "\.exe$", "") -ErrorAction SilentlyContinue) {
        Write-DeployLog ("WARN: {0} is running. Close preview windows if copies fail." -f $proc) $logPath
    }
}

$src = Join-Path $root "media\processed"
if (-not (Test-Path -LiteralPath $src)) {
    $src = Join-Path $root "media"
    Write-DeployLog ("WARN: media\processed not found; trying flat media\: {0}" -f $src) $logPath
}
if (-not (Test-Path -LiteralPath $src)) {
    $src = Join-Path $root "source\media"
    Write-DeployLog ("WARN: bundle-root media not found; trying source\media: {0}" -f $src) $logPath
}

Write-DeployLog ("Source: {0}" -f $src) $logPath
Write-DeployLog ("Target: {0}" -f $dest) $logPath

if (-not (Test-Path -LiteralPath $src)) {
    Write-DeployLog "ERROR: media folder not found." $logPath
    Write-DeployLog "Re-run generate packaging or copy the full ready-to-import bundle, then re-run." $logPath
    Wait-IfNeeded
    exit 1
}

if (-not (Test-Path -LiteralPath $dest)) {
    try {
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
    } catch {
        Write-DeployLog ("ERROR: Could not create {0}" -f $dest) $logPath
        Write-DeployLog "Run as Administrator or check ProgramData permissions." $logPath
        Wait-IfNeeded
        exit 1
    }
}

$previewProbe = Join-Path $src "preview__png.png"
if (Test-Path -LiteralPath $previewProbe) {
    Write-DeployLog "Mode: TouchTools image format test (fixed preview filenames)" $logPath
    $files = @(
        "preview__png.png", "preview__jpg.jpg", "preview__jpeg.jpeg", "preview__bmp.bmp",
        "preview__tif.tif", "preview__tiff.tiff", "preview__webp.webp",
        "preview__gif_static.gif", "preview__gif_anim.gif", "preview__mp4.mp4"
    )
} else {
    Write-DeployLog "Mode: verification / prompt media slots (copy all media files except README.md)" $logPath
    $files = @(Get-ChildItem -LiteralPath $src -File | Where-Object { $_.Name -ne "README.md" } | ForEach-Object { $_.Name })
}

$total = [Math]::Max($files.Count, 1)
$index = 0
foreach ($name in $files) {
    $index++
    $sourceFile = Join-Path $src $name
    $destFile = Join-Path $dest $name
    Write-VisibleProgress -Current $index -Total $total -Item $name

    if (-not (Test-Path -LiteralPath $sourceFile)) {
        Write-DeployLog ("ERROR MISSING  {0}" -f $name) $logPath
        $failed++
        $err = $true
        continue
    }

    $sourceHash = Get-FileSha256 -Path $sourceFile
    if (Test-Path -LiteralPath $destFile) {
        $destHash = Get-FileSha256 -Path $destFile
        if ($sourceHash -eq $destHash) {
            $skipped++
            Write-DeployLog ("SKIP     {0}  already matches destination (sha256)" -f $name) $logPath
            continue
        }
    }

    if (-not (Try-CopyOne -SourceFile $sourceFile -DestFile $destFile)) {
        Write-DeployLog ("ERROR FAILED   {0}  could not copy to {1}" -f $name, $dest) $logPath
        Write-DeployLog "Close FluentControl/VisionX preview, delete or rename the locked file, then re-run." $logPath
        $failed++
        $err = $true
        continue
    }

    $installedHash = Get-FileSha256 -Path $destFile
    if ($installedHash -ne $sourceHash) {
        Write-DeployLog ("ERROR HASH     {0}  installed file hash mismatch" -f $name) $logPath
        $failed++
        $err = $true
        continue
    }

    $copied++
    Write-DeployLog ("OK       {0}  sha256={1}" -f $name, $installedHash) $logPath
}

Write-VisibleProgress -Current $total -Total $total -Item "done" -Final
Write-Host ""

if ($failed -eq 0 -and ($copied -gt 0 -or $skipped -gt 0)) {
    $err = $false
}
if ($copied -eq 0 -and $skipped -eq 0 -and $failed -eq 0) {
    Write-DeployLog ("ERROR: No media files were copied from {0}" -f $src) $logPath
    $err = $true
}

if ($err) {
    Write-DeployLog ("Deploy finished with errors. Copied: {0}, skipped: {1}, failed: {2}." -f $copied, $skipped, $failed) $logPath
    Wait-IfNeeded
    exit 1
}

Write-DeployLog ("Deploy complete. Copied: {0}, skipped: {1}." -f $copied, $skipped) $logPath
Write-DeployLog ("Target: {0}" -f $dest) $logPath
Write-DeployLog "Next: run initialization worktable, then Preview RUP Standard media prompts in Script Editor." $logPath
Wait-IfNeeded
exit 0
