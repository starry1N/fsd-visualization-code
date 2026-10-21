param([string]$Python = '', [string]$OutputDirectory = "$PSScriptRoot\dist")
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\windows_python.ps1"
$buildPython = Find-RadioPython -Requested $Python -RequireDependencies
& $buildPython -m pip install 'PyInstaller==6.22.0'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed' }
& $buildPython -m PyInstaller --noconfirm --distpath $OutputDirectory --workpath "$PSScriptRoot\build\windows-exe" "$PSScriptRoot\windows_receiver.spec"
if ($LASTEXITCODE -ne 0) { throw 'Windows executable build failed' }
Write-Output "Ready: $OutputDirectory\FSD_E22_Receiver.exe"
