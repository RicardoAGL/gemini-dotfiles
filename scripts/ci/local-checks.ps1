Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host '==> PowerShell syntax'
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'scripts\lib.ps1'), [ref]$null, [ref]$null)
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'scripts\install-gemini-cli.ps1'), [ref]$null, [ref]$null)
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'scripts\bootstrap.ps1'), [ref]$null, [ref]$null)
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'scripts\use-project.ps1'), [ref]$null, [ref]$null)
Write-Host 'OK  PowerShell syntax'

Write-Host '==> source-map structure'
$map = Get-Content -Raw (Join-Path $root 'source-map.json') | ConvertFrom-Json
if ($map.global.contextFileName -ne 'GEMINI.md') { throw 'Unexpected context file name.' }
if ($map.global.settingsFileName -ne 'settings.json') { throw 'Unexpected settings file name.' }
Write-Host 'OK  source-map structure'

Write-Host ''
Write-Host 'Local CI baseline passed.'
