param(
    [Parameter(Mandatory = $true)]
    [string]$BundleRoot
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($BundleRoot)
$manifestPath = Join-Path $root "support\delivery_manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "support\delivery_manifest.json is missing"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$deployments = @($manifest.external_file_deployments)
if ($deployments.Count -eq 0) {
    Write-Host "No staged external files to install."
    exit 0
}

foreach ($item in $deployments) {
    $relative = [string]$item.bundle_path
    $target = [string]$item.target_path
    $expected = ([string]$item.sha256).ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($relative) -or [string]::IsNullOrWhiteSpace($target) -or $expected.Length -ne 64) {
        throw "Invalid external-file deployment record"
    }
    $source = Join-Path $root ($relative -replace "/", "\")
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw ("Missing staged external file: " + $source)
    }
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
    if ($sourceHash -ne $expected) {
        throw ("Staged external file hash mismatch: " + $source)
    }
    $parent = Split-Path -Parent $target
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $target -Force
    $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
    if ($targetHash -ne $expected) {
        throw ("Installed external file hash mismatch: " + $target)
    }
    Write-Host ("Installed external file: " + $target)
}
