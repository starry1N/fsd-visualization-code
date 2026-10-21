param([string]$Python = '')
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\windows_python.ps1"
$basePython = Find-RadioPython -Requested $Python
Write-Host "Setup Python: $basePython"
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($basePython -ne $projectPython) {
    & $basePython -m venv "$PSScriptRoot\.venv"
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed' }
}
& "$PSScriptRoot\.venv\Scripts\python.exe" -m pip install -e "${PSScriptRoot}[pc]"
if ($LASTEXITCODE -ne 0) { throw 'Radio dependency installation failed' }
Write-Output 'Ready. Run .\start_windows.ps1 and connect Foxglove to ws://127.0.0.1:8765'
