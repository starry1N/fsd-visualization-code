param([string]$Port = '', [ValidateSet('native','bridge')][string]$Mode = 'native')
$ErrorActionPreference = 'Stop'
$radioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $radioPython)) { throw 'Run setup_windows.ps1 first' }
$receiverArgs = @('-m', 'fsd_radio.cli', '--config', "$PSScriptRoot\radio.yaml", 'pc', '--mode', $Mode)
if ($Port) { $receiverArgs += @('--port', $Port) }
& $radioPython @receiverArgs
exit $LASTEXITCODE
