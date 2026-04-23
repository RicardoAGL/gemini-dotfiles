Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

. (Join-Path $PSScriptRoot 'lib.ps1')

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Project root not found: $ProjectRoot"
}

$hasClaude = Test-Path -LiteralPath (Join-Path $ProjectRoot 'CLAUDE.md')
$hasAgents = Test-Path -LiteralPath (Join-Path $ProjectRoot 'AGENTS.md')
if (-not $hasClaude -and -not $hasAgents) {
    Write-Host "Skipping Gemini wrapper because no CLAUDE.md or AGENTS.md exists in $ProjectRoot"
    exit 0
}

$target = Write-ProjectGeminiWrapper -ProjectRoot $ProjectRoot
Write-Host "Prepared Gemini context wrapper: $target"

