# Shared interpreter discovery. Enumerates known installations, never opens serial ports.
$script:RadioPythonScriptRoot = $PSScriptRoot
function Find-RadioPython {
    param([string]$Requested = '', [switch]$RequireDependencies)
    $candidates = [Collections.Generic.List[string]]::new()
    if ($Requested) {
        $candidates.Add($Requested)
    } else {
        $candidates.Add((Join-Path $script:RadioPythonScriptRoot '.venv\Scripts\python.exe'))
        if ($env:VIRTUAL_ENV) { $candidates.Add((Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe')) }
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($launcher) {
            $previousErrorPreference = $ErrorActionPreference
            try {
                # Older launchers print their installation list to stderr.
                $ErrorActionPreference = 'Continue'
                $installed = @(& $launcher.Source -0p 2>&1)
                foreach ($line in $installed) {
                    if ([string]$line -match '([A-Za-z]:\\.+?python(?:\d+(?:\.\d+)*)?\.exe)\s*$') {
                        $candidates.Add($Matches[1])
                    }
                }
            } catch { } finally { $ErrorActionPreference = $previousErrorPreference }
        }
        foreach ($name in @('python.exe', 'python3.exe')) {
            foreach ($command in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
                $candidates.Add($command.Source)
            }
        }
        foreach ($registryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
            foreach ($versionKey in @(Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue)) {
                $installKey = Get-Item -LiteralPath ($versionKey.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
                if ($installKey) {
                    $executable = $installKey.GetValue('ExecutablePath')
                    if ($executable) { $candidates.Add([string]$executable) }
                    $directory = $installKey.GetValue('')
                    if ($directory) { $candidates.Add((Join-Path $directory 'python.exe')) }
                }
            }
        }
    }
    $probe = 'import sys,struct; (sys.version_info >= (3,10) and struct.calcsize("P")==8) or sys.exit(1)'
    if ($RequireDependencies) {
        $probe += '; import serial,yaml,foxglove; from foxglove.websocket import Capability; from importlib.metadata import version; (version("foxglove-sdk")=="0.29.2" and version("pyserial")=="3.5" and version("PyYAML").split(".")[0]=="6") or sys.exit(1)'
    } else {
        $probe += '; import venv'
    }
    $visited = @{}
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        $executablePath = $command.Source
        if ($executablePath -match '\\Microsoft\\WindowsApps\\' -or $visited.ContainsKey($executablePath)) { continue }
        $visited[$executablePath] = $true
        # An explicit py launcher is allowed for setup; return its actual interpreter.
        try {
            $probeOutput = @(& $executablePath -c ($probe + '; print(sys.executable)') 2>$null)
            if ($LASTEXITCODE -eq 0 -and $probeOutput.Count) {
                $resolvedPython = [string]$probeOutput[-1]
                if (Test-Path -LiteralPath $resolvedPython -PathType Leaf) { return $resolvedPython }
            }
        } catch { }
    }
    if ($RequireDependencies) {
        throw 'No ready 64-bit Python 3.10+ environment found. Run .\setup_windows.ps1 first, or use -Python <python.exe> with installed radio dependencies.'
    }
    throw 'No usable 64-bit Python 3.10+ found. Install Python or specify .\setup_windows.ps1 -Python <python.exe>.'
}
