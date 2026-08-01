[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("fast", "mcp", "simulator", "all")]
    [string]$Suite
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PackageRoot = Join-Path $RepoRoot "source\03-protocol-builder"
$InstallScript = Join-Path $RepoRoot "scripts\install\install.ps1"

function Get-VenvPythonExecutable {
    foreach ($candidate in @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot ".venv/bin/python"),
        (Join-Path $RepoRoot ".venv/bin/python3")
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    return $null
}

function Get-PythonExecutable {
    $venvPython = Get-VenvPythonExecutable
    if ($venvPython) {
        return $venvPython
    }

    foreach ($name in @("python3", "python")) {
        $pythonCommand = Get-Command $name -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            if ($pythonCommand.Source) {
                return $pythonCommand.Source
            }
            return $pythonCommand.Path
        }
    }

    throw "Python 3 was not found. Run scripts\install\install.ps1 first or make sure python3/python is on PATH."
}

function Test-PytestAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable
    )

    & $Executable -m pytest --version *> $null
    return $LASTEXITCODE -eq 0
}

function Ensure-PythonEnvironment {
    $venvPython = Get-VenvPythonExecutable
    if (-not $venvPython) {
        Write-Host ""
        Write-Host "==> bootstrap"
        & $InstallScript -SkipSmokeTest
        if ($LASTEXITCODE -ne 0) {
            throw "Environment bootstrap failed with exit code $LASTEXITCODE."
        }
    }

    $python = Get-PythonExecutable
    if (-not (Test-PytestAvailable -Executable $python)) {
        Write-Host ""
        Write-Host "==> bootstrap"
        & $InstallScript -SkipSmokeTest
        if ($LASTEXITCODE -ne 0) {
            throw "Environment bootstrap failed with exit code $LASTEXITCODE."
        }
        $python = Get-PythonExecutable
        if (-not (Test-PytestAvailable -Executable $python)) {
            throw "pytest is still unavailable after bootstrapping the environment."
        }
    }

    return $python
}

$script:Python = Ensure-PythonEnvironment

function Invoke-CommandLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    $display = $Command -join " "
    Write-Host ""
    Write-Host "==> $WorkingDirectory"
    Write-Host "    $display"

    Push-Location $WorkingDirectory
    try {
        if ($Command.Count -eq 1) {
            & $Command[0]
        } else {
            $commandArgs = $Command[1..($Command.Count - 1)]
            & $Command[0] @commandArgs
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    Invoke-CommandLine -WorkingDirectory $WorkingDirectory -Command (@($script:Python) + $Args)
}

function Invoke-FastSuite {
    Write-Host ""
    Write-Host "==> fast"

    Invoke-Python -WorkingDirectory $PackageRoot -Args @(
        "-m", "tools.sync_readiness_gate_registry",
        "--check"
    )

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "-m", "pytest",
        "source/01-project-reader/tests",
        "source/02-worklist-builder/tests",
        "-q"
    )

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "-m", "pytest",
        "source/03-protocol-builder/tests",
        "-m", "not fluentcontrol_shell",
        "--ignore", "source/03-protocol-builder/tests/test_mcp_gateway.py",
        "-q"
    )

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "-m", "pytest",
        "source/03-protocol-builder/libs/fluentcoder/tests",
        "-m", "not fluentcontrol_shell",
        "-q"
    )
}

function Invoke-McpSuite {
    Write-Host ""
    Write-Host "==> mcp"

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "-m", "pytest",
        "source/03-protocol-builder/tests/test_mcp_gateway.py",
        "source/03-protocol-builder/tests/test_architecture_import_paths.py",
        "-q"
    )

    Invoke-Python -WorkingDirectory $PackageRoot -Args @(
        "-m", "fluent_pipeline.mcp_server",
        "--self-test"
    )

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "scripts/mcp/smoke_mcp.py"
    )
}

function Invoke-SimulatorSuite {
    Write-Host ""
    Write-Host "==> simulator"

    Invoke-Python -WorkingDirectory $RepoRoot -Args @(
        "-m", "pytest",
        "source/03-protocol-builder/tests",
        "source/03-protocol-builder/libs/fluentcoder/tests",
        "-m", "fluentcontrol_shell",
        "-q"
    )
}

switch ($Suite) {
    "fast" { Invoke-FastSuite }
    "mcp" { Invoke-McpSuite }
    "simulator" { Invoke-SimulatorSuite }
    "all" {
        Invoke-FastSuite
        Invoke-McpSuite
        Invoke-SimulatorSuite
    }
    default {
        throw "Unknown suite: $Suite"
    }
}
