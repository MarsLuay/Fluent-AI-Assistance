[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\test-suite.ps1" -Suite mcp
