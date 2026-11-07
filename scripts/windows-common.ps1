Set-StrictMode -Version Latest

function Assert-WindowsHost {
    if ($env:OS -ne 'Windows_NT') {
        throw 'This script must run on your Windows PC, not in WSL or on a cloud host.'
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WslExecutable {
    $command = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'WSL is not available. Run Setup-Windows.cmd first.' }
    return $command.Source
}

function Get-UsbipdExecutable {
    $command = Get-Command usbipd.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $path = Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe'
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    throw 'usbipd-win is not installed. Run Setup-Windows.cmd first.'
}

function Invoke-CheckedNative {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed (exit $LASTEXITCODE)."
    }
}

function Get-WslDistributions {
    param([string]$Executable)
    $lines = & $Executable --list --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list WSL distributions. Run Setup-Windows.cmd.' }
    return @(($lines -join "`n" -replace "`0", '') -split "`n" |
        ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Assert-Wsl2Runtime {
    param([string]$Executable, [string]$Distro, [string]$LinuxUser)
    if (@(Get-WslDistributions $Executable) -notcontains $Distro) {
        throw "WSL distribution '$Distro' is missing. Run Setup-Windows.cmd."
    }
    $kernel = & $Executable --distribution $Distro --user $LinuxUser --exec uname -r
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot start WSL user '$LinuxUser'. Run Setup-Windows.cmd."
    }
    if (($kernel -join '') -notmatch 'WSL2') {
        throw "The selected distribution must run under WSL2 (kernel: $kernel)."
    }
}

function Get-WslPythonExecutable {
    param([string]$Executable, [string]$Distro, [string]$LinuxUser)
    $record = & $Executable --distribution $Distro --user $LinuxUser --exec getent passwd $LinuxUser
    if ($LASTEXITCODE -ne 0) { throw "Cannot find WSL user '$LinuxUser'. Run Setup-Windows.cmd." }
    $linuxHome = (($record -join '').Trim() -split ':')[5]
    $python = "$linuxHome/.local/share/multishutter/venv/bin/python"
    & $Executable --distribution $Distro --user $LinuxUser --exec test -x $python
    if ($LASTEXITCODE -ne 0) { throw 'The camera Python environment is missing. Run Setup-Windows.cmd.' }
    return $python
}
