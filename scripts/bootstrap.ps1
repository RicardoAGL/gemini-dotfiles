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
Remove-GovernanceAgyx

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $pythonCommand) {
    throw 'Python is required to establish Gemini governance and skill parity.'
}
$agyCommand = Get-Command agy -CommandType Application -ErrorAction SilentlyContinue
if (-not $agyCommand) {
    throw 'agy is required to install the Antigravity governance plugin.'
}
$governanceInstaller = Join-Path $PSScriptRoot 'install-governance-plugin.py'
$governancePlugin = Join-Path (Get-GeminiDotfilesRoot) 'plugins\claude-governance'
& $pythonCommand.Source $governanceInstaller --agy $agyCommand.Source --plugin-dir $governancePlugin
if ($LASTEXITCODE -ne 0) {
    throw "Antigravity governance plugin install failed with exit code $LASTEXITCODE."
}
Install-GovernanceAgyx -PythonPath $pythonCommand.Source -InstallerPath $governanceInstaller -PluginPath $governancePlugin -AgyPath $agyCommand.Source

& $pythonCommand.Source (Join-Path $PSScriptRoot 'sync-claude-plugins.py')
if ($LASTEXITCODE -ne 0) {
    throw "Gemini skill sync failed with exit code $LASTEXITCODE."
}

Write-Host "Global GEMINI.md: $mode -> $targetContext"
Write-Host "Managed settings: $(Get-GeminiSettingsPath)"
Write-Host 'Windows wrapper ensured: gem -> gemini --approval-mode=yolo'
Write-Host 'Windows wrapper ensured: agyx -> verified governance + agy --dangerously-skip-permissions'
Write-Host 'Shared wrapper ensured: infisical-safe-wrapper'
