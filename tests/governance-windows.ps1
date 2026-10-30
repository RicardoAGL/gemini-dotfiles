Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'scripts\lib.ps1')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$python3Only = {
    param([string]$Name)
    if ($Name -eq 'python3') {
        return [pscustomobject]@{ Source = 'C:\Python3\python3.exe' }
    }
    return $null
}
$pythonCommand = Resolve-PythonCommand -Resolver $python3Only
Assert-True ($pythonCommand.Source -eq 'C:\Python3\python3.exe') 'python3-only fallback was not selected.'
$infisicalCmd = New-InfisicalCmdContent -PythonPath $pythonCommand.Source
Assert-True ($infisicalCmd.Contains('"C:\Python3\python3.exe"')) 'Infisical CMD did not embed the validated Python executable.'
Assert-True (-not ($infisicalCmd -match '(?m)^python\s')) 'Infisical CMD retained an unvalidated hard-coded python command.'

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("governance-windows-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $common = @{
        PythonPath = 'C:\Python3\python3.exe'
        InstallerPath = 'C:\repo\install-governance-plugin.py'
        PluginPath = 'C:\repo\plugins\claude-governance'
        AgyPath = 'C:\bin\agy.cmd'
        BinDir = $temporary
    }

    $foreignPs1 = Join-Path $temporary 'agyx.ps1'
    [IO.File]::WriteAllText($foreignPs1, "Write-Output 'foreign'`r`n")
    $failed = $false
    try { Remove-GovernanceAgyx @common } catch { $failed = $true }
    Assert-True $failed 'Unmanaged agyx.ps1 collision did not fail.'
    Assert-True (Test-Path -LiteralPath $foreignPs1) 'Unmanaged agyx.ps1 was deleted.'
    Remove-Item -LiteralPath $foreignPs1

    Install-GovernanceAgyx @common
    Remove-GovernanceAgyx @common
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $temporary 'agyx.cmd'))) 'Owned agyx.cmd was not removed.'
    $quarantine = Get-ChildItem -LiteralPath (Join-Path $temporary '.gemini-dotfiles-quarantine') -Filter 'agyx.cmd.*'
    Assert-True ($quarantine.Count -eq 1) 'Owned agyx.cmd was not quarantined exactly once.'

    $foreignCmd = Join-Path $temporary 'agyx.cmd'
    [IO.File]::WriteAllText($foreignCmd, "@echo off`r`necho foreign`r`n")
    $failed = $false
    try { Remove-GovernanceAgyx @common } catch { $failed = $true }
    Assert-True $failed 'Foreign agyx.cmd collision did not fail.'
    Assert-True (([IO.File]::ReadAllText($foreignCmd)) -match 'foreign') 'Foreign agyx.cmd was modified.'
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}

Write-Host 'Governance Windows boundary tests passed.'
