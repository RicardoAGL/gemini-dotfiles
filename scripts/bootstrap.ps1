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

Write-Host "Global GEMINI.md: $mode -> $targetContext"
Write-Host "Managed settings: $(Get-GeminiSettingsPath)"
Write-Host 'Windows wrapper ensured: gem -> gemini --approval-mode=yolo'
Write-Host 'Shared wrapper ensured: infisical-safe-wrapper'
