Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-GeminiDotfilesRoot {
    return Split-Path -Parent $PSScriptRoot
}

function Get-WorkspaceRoot {
    return Split-Path -Parent (Get-GeminiDotfilesRoot)
}

function Get-SourceMap {
    $root = Get-GeminiDotfilesRoot
    $localPath = Join-Path $root 'source-map.local.json'
    $basePath = if (Test-Path -LiteralPath $localPath) { $localPath } else { Join-Path $root 'source-map.json' }
    return Get-Content -Raw $basePath | ConvertFrom-Json
}

function Get-CanonicalSourceMap {
    $path = Join-Path (Get-GeminiDotfilesRoot) 'source-map.json'
    return Get-Content -Raw $path | ConvertFrom-Json
}

function ConvertTo-PlainHashtable {
    param([Parameter(Mandatory = $true)]$Value)

    if ($null -eq $Value) {
        return $null
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $result = @{}
        foreach ($key in $Value.Keys) {
            $result[$key] = ConvertTo-PlainHashtable -Value $Value[$key]
        }
        return $result
    }

    if ($Value -is [pscustomobject]) {
        $result = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $result[$property.Name] = ConvertTo-PlainHashtable -Value $property.Value
        }
        return $result
    }

    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = New-Object System.Collections.ArrayList
        foreach ($item in $Value) {
            [void]$items.Add((ConvertTo-PlainHashtable -Value $item))
        }
        return ,$items.ToArray()
    }

    return $Value
}

function Get-SourceMapValue {
    param([Parameter(Mandatory = $true)][string]$PathKey)

    $value = Get-SourceMap
    foreach ($part in ($PathKey -split '\.')) {
        $value = $value.$part
    }
    return [string]$value
}

function Get-CanonicalSourceMapValue {
    param([Parameter(Mandatory = $true)][string]$PathKey)
    $value = Get-CanonicalSourceMap
    foreach ($part in ($PathKey -split '\.')) {
        $value = $value.$part
    }
    return [string]$value
}

function Get-GeminiHome {
    return Join-Path $HOME (Get-SourceMapValue -PathKey 'global.geminiHomeDirName')
}

function Get-GeminiContextPath {
    return Join-Path (Get-GeminiHome) (Get-SourceMapValue -PathKey 'global.contextFileName')
}

function Get-GeminiSettingsPath {
    return Join-Path (Get-GeminiHome) (Get-SourceMapValue -PathKey 'global.settingsFileName')
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Get-CanonicalRepoPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    return Join-Path (Get-WorkspaceRoot) $Name
}

function Get-WorkspacePathFromSourceMap {
    param([Parameter(Mandatory = $true)][string]$PathKey)
    return Get-SafeWorkspaceRepoPath -Name (Get-SourceMapValue -PathKey $PathKey)
}

function Assert-SafeWorkspaceRepoName {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ($Name -in @('.', '..') -or $Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
        throw "Unsafe workspace repository name: $Name"
    }
}

function Assert-NoReparseComponents {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $relative = $fullPath.Substring($root.Length)
    $current = $root
    foreach ($part in ($relative -split '[\\/]' | Where-Object { $_ })) {
        $current = Join-Path $current $part
        if (-not (Test-Path -LiteralPath $current)) { continue }
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Redirecting path component is not allowed: $current"
        }
    }
}

function Get-SafeWorkspaceRepoPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    Assert-SafeWorkspaceRepoName -Name $Name
    $workspace = [System.IO.Path]::GetFullPath((Get-WorkspaceRoot)).TrimEnd('\', '/')
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $workspace $Name))
    $prefix = $workspace + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Repository path escapes the workspace: $candidate"
    }
    Assert-NoReparseComponents -Path $candidate
    return $candidate
}

function Get-UserBinDir {
    return Join-Path $HOME 'bin'
}

function Get-MarkdownSection {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Heading
    )

    $content = Get-Content -Raw $Path
    $escapedHeading = [regex]::Escape($Heading)
    $pattern = "(?ms)^$escapedHeading\r?\n.*?(?=^##\s|\z)"
    $match = [regex]::Match($content, $pattern)
    if (-not $match.Success) {
        throw "Heading '$Heading' not found in $Path"
    }
    return $match.Value.TrimEnd()
}

function Write-GeneratedGlobalGemini {
    $root = Get-GeminiDotfilesRoot
    $map = Get-SourceMap
    $outputPath = Join-Path $root 'generated\global\GEMINI.md'
    Ensure-Directory -Path (Split-Path -Parent $outputPath)

    $claudeRoot = Get-CanonicalRepoPath -Name $map.canonical.claudeDotfilesRepo
    $securityRoot = Get-CanonicalRepoPath -Name $map.canonical.securityBaselineRepo
    $tokenGuardRoot = Get-CanonicalRepoPath -Name $map.canonical.tokenGuardRepo
    $claudeGlobal = Join-Path $claudeRoot 'claude\CLAUDE.md'
    $claudePolicies = Join-Path $claudeRoot 'claude\policies.md'
    $securitySnippet = Join-Path $securityRoot 'configs\claude-code\CLAUDE.md.snippet'
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'

    $lines = @(
        '# Global Gemini Instructions',
        '',
        "> Generated by `gemini-dotfiles/scripts/bootstrap` on $timestamp.",
        '',
        'Claude remains the canonical source of truth. This file translates the portable global Claude rules into Gemini CLI''s default `GEMINI.md` memory location.',
        '',
        '## Canonical Sources',
        '',
        ('- `' + $claudeGlobal + '`'),
        ('- `' + $claudePolicies + '`'),
        ('- `' + $securitySnippet + '`'),
        ('- `' + (Join-Path $tokenGuardRoot 'README.md') + '`'),
        '',
        '## Gemini Operating Model',
        '',
        '- Treat this file as the global Gemini memory baseline.',
        '- Prefer repo-local `GEMINI.md` wrappers that import `CLAUDE.md` or `AGENTS.md` instead of duplicating project instructions.',
        '- Do not mirror machine-local Claude settings files.',
        '',
        '## Portable Rules',
        ''
    )

    foreach ($heading in $map.translation.globalGeminiSections) {
        $lines += Get-MarkdownSection -Path $claudeGlobal -Heading $heading
        $lines += ''
    }

    $lines += @(
        '## Security Baseline Reference',
        '',
        ('Portable security rules also come from `' + $securitySnippet + '`.'),
        'Project-specific protected-path guidance should still come from repo-local security files.',
        '',
        '## Detailed Reference',
        '',
        ('For procedures not copied into this file, read `' + $claudePolicies + '` on demand.')
    )

    Set-Content -LiteralPath $outputPath -Value ($lines -join [Environment]::NewLine)
    return $outputPath
}

function New-LinkOrCopy {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    Ensure-Directory -Path (Split-Path -Parent $Target)

    if (Test-Path -LiteralPath $Target) {
        $backup = "$Target.pre-gemini-dotfiles.bak"
        if (-not (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $Target -Destination $backup
        } else {
            Remove-Item -LiteralPath $Target -Force -Recurse
        }
    }

    try {
        New-Item -ItemType SymbolicLink -Path $Target -Target $Source -Force | Out-Null
        return 'linked'
    } catch {
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        return 'copied'
    }
}

function Merge-ManagedSettings {
    param([Parameter(Mandatory = $true)][string]$Path)

    Ensure-Directory -Path (Split-Path -Parent $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        Set-Content -LiteralPath $Path -Value '{}' -NoNewline
    }

    $currentRaw = Get-Content -Raw $Path
    $current = if ($currentRaw.Trim()) { ConvertTo-PlainHashtable -Value ($currentRaw | ConvertFrom-Json) } else { @{} }
    $managed = @{
        general = @{
            defaultApprovalMode = 'default'
            plan = @{
                enabled = $true
                modelRouting = $true
            }
        }
        privacy = @{
            usageStatisticsEnabled = $false
        }
        context = @{
            fileName = 'GEMINI.md'
        }
        security = @{
            environmentVariableRedaction = @{
                enabled = $true
            }
        }
    }

    function Merge-Hashtable {
        param(
            [Parameter(Mandatory = $true)][hashtable]$Base,
            [Parameter(Mandatory = $true)][hashtable]$Override
        )

        $result = @{}
        foreach ($key in $Base.Keys) { $result[$key] = $Base[$key] }
        foreach ($key in $Override.Keys) {
            if ($result[$key] -is [hashtable] -and $Override[$key] -is [hashtable]) {
                $result[$key] = Merge-Hashtable -Base $result[$key] -Override $Override[$key]
            } else {
                $result[$key] = $Override[$key]
            }
        }
        return $result
    }

    $merged = Merge-Hashtable -Base $current -Override $managed
    Set-Content -LiteralPath $Path -Value ($merged | ConvertTo-Json -Depth 10)
}

function Ensure-PowerShellGemFunction {
    $profilePath = $PROFILE.CurrentUserAllHosts
    Ensure-Directory -Path (Split-Path -Parent $profilePath)
    if (-not (Test-Path -LiteralPath $profilePath)) {
        New-Item -ItemType File -Path $profilePath -Force | Out-Null
    }

    $content = Get-Content -Raw $profilePath
    if ($content -notmatch '(?m)^function gem \{') {
        Add-Content -LiteralPath $profilePath -Value @"

function gem {
    gemini --approval-mode=yolo @args
}
"@
    }
}

function Ensure-WindowsGemWrapper {
    $binDir = Join-Path $HOME 'bin'
    Ensure-Directory -Path $binDir

    $wrapperPath = Join-Path $binDir 'gem.cmd'
    $wrapperContent = "@echo off`r`n""%AppData%\npm\gemini.cmd"" --approval-mode=yolo %*`r`n"
    Set-Content -LiteralPath $wrapperPath -Value $wrapperContent -NoNewline

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $binDirNormalized = $binDir.TrimEnd('\')
    $pathEntries = @()
    if ($userPath) {
        $pathEntries = $userPath -split ';' | Where-Object { $_ }
    }
    if ($pathEntries -notcontains $binDirNormalized) {
        $newPath = if ($userPath) { $userPath.TrimEnd(';') + ';' + $binDirNormalized } else { $binDirNormalized }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    }
}

function Remove-GovernanceAgyx {
    $binDir = Get-UserBinDir
    foreach ($name in @('agyx.cmd', 'agyx.ps1')) {
        $candidate = Join-Path $binDir $name
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Force
        }
    }
}

function Install-GovernanceAgyx {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$InstallerPath,
        [Parameter(Mandatory = $true)][string]$PluginPath,
        [Parameter(Mandatory = $true)][string]$AgyPath
    )

    foreach ($value in @($PythonPath, $InstallerPath, $PluginPath, $AgyPath)) {
        if ($value -match '[\r\n"%!^&|<>()]') {
            throw 'Governance wrapper paths contain unsupported cmd.exe metacharacters.'
        }
    }

    $binDir = Get-UserBinDir
    Ensure-Directory -Path $binDir
    $wrapperPath = Join-Path $binDir 'agyx.cmd'
    $wrapperContent = @"
@echo off
"$PythonPath" "$InstallerPath" --check-only --agy "$AgyPath" --plugin-dir "$PluginPath" >nul
if errorlevel 1 exit /b %errorlevel%
"$AgyPath" --dangerously-skip-permissions %*
"@
    Set-Content -LiteralPath $wrapperPath -Value $wrapperContent -NoNewline
}

function Ensure-InfisicalSafeWrapper {
    $wrapperRepo = Get-WorkspacePathFromSourceMap -PathKey 'integrations.infisicalWrapper.repoPath'
    if (-not (Test-Path -LiteralPath $wrapperRepo -PathType Container)) {
        Write-Host "Skipping infisical-safe-wrapper install because repo is missing: $wrapperRepo"
        return
    }
    $binDir = Get-UserBinDir
    Ensure-Directory -Path $binDir
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python is required to install infisical-safe-wrapper.' }
    $expectedRemote = Get-CanonicalSourceMapValue -PathKey 'integrations.infisicalWrapper.remoteUrl'
    $commit = (& git -C $wrapperRepo rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -cnotmatch '^[0-9a-f]{40}$') {
        throw 'Refusing infisical-safe-wrapper install: checkout is not a valid Git repository.'
    }
    $runtimeSource = Join-Path (Get-GeminiDotfilesRoot) 'scripts\integrations\infisical-wrapper-runtime.py'
    $runtimePath = Join-Path $binDir '.infisical-safe-wrapper-runtime.py'
    $configPath = Join-Path $binDir '.infisical-safe-wrapper.json'
    $cmdPath = Join-Path $binDir 'infisical-safe-wrapper.cmd'
    $ps1Path = Join-Path $binDir 'infisical-safe-wrapper.ps1'
    $suffix = [Guid]::NewGuid().ToString('N')
    $runtimeTemp = "$runtimePath.tmp.$suffix"
    $configTemp = "$configPath.tmp.$suffix"
    $cmdTemp = "$cmdPath.tmp.$suffix"
    $ps1Temp = "$ps1Path.tmp.$suffix"
    try {
        Copy-Item -LiteralPath $runtimeSource -Destination $runtimeTemp
        $config = [ordered]@{
            schemaVersion = 1
            repo = $wrapperRepo
            expectedRemote = $expectedRemote
            expectedCommit = $commit
            entrypoint = 'bin/infisical-safe-wrapper.ps1'
            executor = 'powershell'
        }
        Set-Content -LiteralPath $configTemp -Value (($config | ConvertTo-Json -Compress) + "`n") -NoNewline
        & $pythonCommand.Source $runtimeTemp --verify-only $configTemp
        if ($LASTEXITCODE -ne 0) { throw 'Infisical wrapper provenance verification failed.' }
        $cmdContent = "@echo off`r`npython ""%~dp0\.infisical-safe-wrapper-runtime.py"" ""%~dp0\.infisical-safe-wrapper.json"" %*`r`n"
        $ps1Content = @'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pythonCommand) { throw 'Python is required to run infisical-safe-wrapper.' }
& $pythonCommand.Source (Join-Path $PSScriptRoot '.infisical-safe-wrapper-runtime.py') (Join-Path $PSScriptRoot '.infisical-safe-wrapper.json') @args
exit $LASTEXITCODE
'@
        Set-Content -LiteralPath $cmdTemp -Value $cmdContent -NoNewline
        Set-Content -LiteralPath $ps1Temp -Value $ps1Content -NoNewline
        Move-Item -Force -LiteralPath $runtimeTemp -Destination $runtimePath
        Move-Item -Force -LiteralPath $configTemp -Destination $configPath
        Move-Item -Force -LiteralPath $cmdTemp -Destination $cmdPath
        Move-Item -Force -LiteralPath $ps1Temp -Destination $ps1Path
    }
    finally {
        foreach ($temporary in @($runtimeTemp, $configTemp, $cmdTemp, $ps1Temp)) {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -Force -LiteralPath $temporary }
        }
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $binDirNormalized = $binDir.TrimEnd('\')
    $pathEntries = @()
    if ($userPath) {
        $pathEntries = $userPath -split ';' | Where-Object { $_ }
    }
    if ($pathEntries -notcontains $binDirNormalized) {
        $newPath = if ($userPath) { $userPath.TrimEnd(';') + ';' + $binDirNormalized } else { $binDirNormalized }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    }

    Write-Host "Installed infisical-safe-wrapper PATH wrappers in $binDir"
}

function Write-ProjectGeminiWrapper {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $target = Join-Path $ProjectRoot 'GEMINI.md'
    if (Test-Path -LiteralPath $target) {
        return $target
    }

    $lines = @(
        '# Gemini Project Context',
        '',
        'This repository is still Claude-first. Gemini CLI should load the canonical project instructions from the existing root files below instead of creating a competing instruction source.',
        ''
    )
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'AGENTS.md')) {
        $lines += '@./AGENTS.md'
    }
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'CLAUDE.md')) {
        $lines += '@./CLAUDE.md'
    }
    Set-Content -LiteralPath $target -Value ($lines -join [Environment]::NewLine)
    return $target
}
