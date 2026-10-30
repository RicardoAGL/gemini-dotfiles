Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib.ps1')

$generatedContext = Write-GeneratedGlobalGemini
$targetContext = Get-GeminiContextPath
$mode = New-LinkOrCopy -Source $generatedContext -Target $targetContext

Merge-ManagedSettings -Path (Get-GeminiSettingsPath)
Ensure-PowerShellGemFunction
Ensure-WindowsGemWrapper
Ensure-InfisicalSafeWrapper

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $pythonCommand) {
    throw 'Python is required to establish Gemini skill parity.'
}
& $pythonCommand.Source (Join-Path $PSScriptRoot 'sync-claude-plugins.py')
if ($LASTEXITCODE -ne 0) {
    throw "Gemini skill sync failed with exit code $LASTEXITCODE."
}

Write-Host "Global GEMINI.md: $mode -> $targetContext"
Write-Host "Managed settings: $(Get-GeminiSettingsPath)"
Write-Host 'Windows wrapper ensured: gem -> gemini --approval-mode=yolo'
Write-Host 'Shared wrapper ensured: infisical-safe-wrapper'
