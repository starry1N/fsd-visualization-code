param([string]$Port = '', [ValidateSet('native','bridge')][string]$Mode = 'native', [string]$Python = '')
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\windows_python.ps1"
$radioPython = Find-RadioPython -Requested $Python -RequireDependencies
Write-Host "Radio Python: $radioPython"
$receiverArgs = @('-m', 'fsd_radio.cli', '--config', "$PSScriptRoot\radio.yaml", 'pc', '--mode', $Mode)
if ($Port) { $receiverArgs += @('--port', $Port) }
Push-Location $PSScriptRoot
try {
    & $radioPython @receiverArgs
    $receiverExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $receiverExitCode
