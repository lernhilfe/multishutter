[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')]
    [string]$Distro = 'Ubuntu-24.04',
    [ValidatePattern('^[a-z_][a-z0-9_-]*$')]
    [string]$LinuxUser = 'multishutter',
    [switch]$SystemOnly
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')
try {
    Assert-WindowsHost
    if ($SystemOnly) {
        if (-not (Test-IsAdministrator)) { throw 'The system setup stage requires administrator privileges.' }
        $restart = $false
        foreach ($name in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
            $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
            if ($feature.State -eq 'EnablePending') {
                $restart = $true
            } elseif ($feature.State -ne 'Enabled') {
                $result = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart
                if ($result.RestartNeeded) { $restart = $true }
            }
        }
        $usbipd = $null
        try { $usbipd = Get-UsbipdExecutable } catch { }
        if (-not $usbipd) {
            $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
            if (-not $winget) { throw 'Install Microsoft App Installer (winget) or usbipd-win, then rerun setup.' }
            # Interactive MSI avoids an unattended reboot by the USB driver installer.
            & $winget.Source install --interactive --exact --id dorssel.usbipd-win --source winget `
                --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 3010) { $restart = $true }
            elseif ($LASTEXITCODE -ne 0) { throw "usbipd-win installation failed (exit $LASTEXITCODE)." }
        }
        if ($restart) { exit 3010 }
        $wsl = Get-WslExecutable
        & $wsl --install --no-distribution
        if ($LASTEXITCODE -eq 3010) { exit 3010 }
        if ($LASTEXITCODE -ne 0) { throw "WSL runtime installation failed (exit $LASTEXITCODE)." }
        exit 0
    }

    Write-Host 'Setting up WSL2 and USB support. Windows may request administrator access.'
    # Only system components are installed in the elevated process. The distribution
    # remains registered to the original Windows user, even with a separate admin account.
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $elevatedArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -SystemOnly' -f $PSCommandPath
    $system = Start-Process -FilePath $powerShell -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $elevatedArgs
    if ($system.ExitCode -eq 3010) {
        Write-Host 'A Windows restart is required. Restart when convenient, then run Setup-Windows.cmd again.'
        exit 3010
    }
    if ($system.ExitCode -ne 0) { throw 'Windows component setup failed. No application was installed.' }
    $wsl = Get-WslExecutable
    Invoke-CheckedNative $wsl @('--set-default-version', '2')
    $distros = @(Get-WslDistributions $wsl)
    if ($distros -notcontains $Distro) {
        & $wsl --install --distribution $Distro --no-launch
        if ($LASTEXITCODE -eq 3010) {
            Write-Host 'Restart Windows, then run Setup-Windows.cmd again.'
            exit 3010
        }
        if ($LASTEXITCODE -ne 0) { throw "Cannot install $Distro (exit $LASTEXITCODE)." }
    }
    # Do not silently convert an existing WSL1 distribution or interrupt its workloads.
    $kernel = & $wsl --distribution $Distro --user root --exec uname -r
    if ($LASTEXITCODE -ne 0 -or ($kernel -join '') -notmatch 'WSL2') {
        throw "'$Distro' must use WSL2. Check 'wsl --list --verbose'; convert it explicitly if needed."
    }
    Invoke-CheckedNative $wsl @('--distribution', $Distro, '--user', 'root', '--cd', $PSScriptRoot,
        '--exec', 'bash', 'scripts/bootstrap-wsl.sh', $LinuxUser)
    Write-Host 'Running diagnostics and automated tests. No physical camera is triggered.'
    $python = Get-WslPythonExecutable $wsl $Distro $LinuxUser
    Invoke-CheckedNative $wsl @('--distribution', $Distro, '--user', $LinuxUser, '--cd', $PSScriptRoot,
        '--exec', $python, '-m', 'multishutter', 'doctor')
    Invoke-CheckedNative $wsl @('--distribution', $Distro, '--user', $LinuxUser, '--cd', $PSScriptRoot,
        '--exec', $python, '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Write-Host 'Setup finished. MultiShutter runs from this folder; no app service or autostart was created.'
    Write-Host 'Next: Start-MultiShutter.cmd -Simulated, then follow WINDOWS.md to forward your cameras.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
