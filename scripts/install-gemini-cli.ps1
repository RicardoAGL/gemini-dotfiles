Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is required to install Gemini CLI.'
}

& npm install -g @google/gemini-cli
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install Gemini CLI.'
}

& gemini --version

