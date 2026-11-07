[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxUser = 'multishutter',
    [ValidateSet('console', 'doctor', 'discover', 'check', 'capture')]
    [string]$Command = 'console',
    [string]$Config = '',
    [switch]$Simulated,
    [int]$Takes = 1,
    [double]$Countdown = 3,
    [double]$Interval = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')
try {
    Assert-WindowsHost
    $wsl = Get-WslExecutable
    Assert-Wsl2Runtime $wsl $Distro $LinuxUser
    $python = Get-WslPythonExecutable $wsl $Distro $LinuxUser
    if (-not $Config) {
        $Config = 'config/rig.local.toml'
        if ($Simulated) { $Config = 'config/rig.simulated.toml' }
    }
    # --cd and argument arrays preserve spaces and non-ASCII Windows paths.
    $cli = @($Command)
    $backend = 'gphoto2'
    if ($Simulated) { $backend = 'simulated' }
    if ($Command -in @('doctor', 'discover', 'console')) {
        $cli += @('--backend', $backend)
    }
    if ($Command -in @('console', 'check', 'capture')) {
        $cli += @('--config', $Config)
    }
    if ($Command -in @('console', 'capture')) {
        $cli += @('--output', 'captures')
    }
    if ($Command -eq 'capture') {
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $cli += @('--takes', $Takes.ToString($culture), '--countdown', $Countdown.ToString($culture),
                  '--interval', $Interval.ToString($culture))
    }
    $nativeArgs = @('--distribution', $Distro, '--user', $LinuxUser, '--cd', $PSScriptRoot,
                    '--exec', $python, '-m', 'multishutter') + $cli
    & $wsl @nativeArgs
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
