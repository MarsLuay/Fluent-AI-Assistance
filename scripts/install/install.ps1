[CmdletBinding()]
param(
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PackageRoot = Join-Path $RepoRoot "source\03-protocol-builder"
$VenvRoot = Join-Path $RepoRoot ".venv"

function Get-VenvPythonExecutable {
    foreach ($candidate in @(
        (Join-Path $VenvRoot "Scripts\python.exe"),
        (Join-Path $VenvRoot "bin/python"),
        (Join-Path $VenvRoot "bin/python3")
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    return $null
}

$Python = Get-VenvPythonExecutable

if (-not $Python) {
    $Created = $false
    foreach ($SystemPythonName in @("python3", "python")) {
        $SystemPython = Get-Command $SystemPythonName -ErrorAction SilentlyContinue
        if ($SystemPython) {
            $SystemPythonExecutable = $SystemPython.Source
            if (-not $SystemPythonExecutable) {
                $SystemPythonExecutable = $SystemPython.Path
            }
            & $SystemPythonExecutable -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -eq 0) {
                & $SystemPythonExecutable -m venv $VenvRoot
                $Created = $LASTEXITCODE -eq 0
            }
            if ($Created) {
                break
            }
        }
    }
    if (-not $Created) {
        $Launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $Launcher) {
            throw "Python 3.11 or newer is required."
        }
        $LauncherExecutable = $Launcher.Source
        if (-not $LauncherExecutable) {
            $LauncherExecutable = $Launcher.Path
        }
        & $LauncherExecutable -3 -m venv $VenvRoot
        $Created = $LASTEXITCODE -eq 0
    }
    if ($Created) {
        $Python = Get-VenvPythonExecutable
    }
    if (-not $Created -or -not $Python) {
        throw "Could not create a virtual environment with Python 3.11 or newer."
    }
}

Push-Location $PackageRoot
try {
    & $Python -m fluent_pipeline.bootstrap
}
finally {
    Pop-Location
}

if (-not $SkipSmokeTest) {
    Write-Host ""
    Write-Host "==> MCP self-test"
    & $Python -m fluent_pipeline.mcp_server --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "MCP self-test failed with exit code $LASTEXITCODE (required tools / bootstrap next_step)."
    }

    Write-Host ""
    Write-Host "==> MCP smoke (status + bootstrap)"
    & $Python (Join-Path $RepoRoot "scripts\mcp\smoke_mcp.py")
    if ($LASTEXITCODE -ne 0) {
        throw "MCP smoke failed with exit code $LASTEXITCODE (missing tools or bootstrap next_step)."
    }

    Write-Host ""
    Write-Host "==> CLI bootstrap-status"
    Push-Location $PackageRoot
    try {
        & $Python -m fluent_pipeline.cli bootstrap-status --no-report | Out-Null
        # 0 = doctor ok, 1 = doctor failed but next_step still returned; >=2 = hard error
        if ($LASTEXITCODE -gt 1) {
            throw "CLI bootstrap-status failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$ConfigDir = Join-Path $RepoRoot ".mcp"
$ConfigPath = Join-Path $ConfigDir "server-config.json"
New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
$Config = [ordered]@{
    mcpServers = [ordered]@{
        "fluent-ai-assistance" = [ordered]@{
            command = $Python
            args = @("-m", "fluent_pipeline.mcp_server")
            cwd = $PackageRoot
            env = [ordered]@{
                PYTHONUTF8 = "1"
            }
        }
    }
}
$Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ConfigPath -Encoding utf8

Write-Host ""
Write-Host "Fluent AI-Assistance MCP is installed."
Write-Host "Client configuration: $ConfigPath"
Write-Host "Add the fluent-ai-assistance entry to your AI client's MCP configuration, reload the client, then call fluent_status and fluent_bootstrap_status."
