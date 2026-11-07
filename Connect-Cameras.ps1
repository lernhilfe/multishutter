[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+-[0-9]+(,[0-9]+-[0-9]+)*$')]
    [string]$BusId = '',
    [switch]$Bind,
    [switch]$Force,
    [switch]$Detach,
    [switch]$Unbind,
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxUser = 'multishutter'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')
try {
    Assert-WindowsHost
    if ($Force -and -not $Bind) { throw '-Force requires -Bind.' }
    $usbipd = Get-UsbipdExecutable
    if (@(@($Bind, $Detach, $Unbind) | Where-Object { $_ }).Count -gt 1) {
        throw 'Choose only one of -Bind, -Detach or -Unbind.'
    }
    $devices = & $usbipd list
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list USB devices.' }
    $devices | ForEach-Object { Write-Host $_ }
    $selected = @()
    if ($BusId) { $selected = @($BusId.Split(',')) }
    if ($selected.Count -eq 0) {
        Write-Host 'Choose the Sony camera BUSIDs from this list; no USB device was changed.'
        Write-Host 'Share (requests admin access): .\Connect-Cameras.cmd -BusId 1-2,1-3 -Bind'
        Write-Host 'Attach to WSL: .\Connect-Cameras.cmd -BusId 1-2,1-3'
        exit 0
    }
    if (@($selected | Select-Object -Unique).Count -ne $selected.Count) {
        throw 'Each BUSID must be selected only once.'
    }
    foreach ($id in $selected) {
        # Never forward unrelated USB devices or entire hubs automatically.
        $pattern = '^\s*' + [regex]::Escape($id) + '\s+054c:'
        if (-not @($devices | Where-Object { $_ -match $pattern }).Count) {
            throw "BUSID '$id' is not a connected Sony USB device (vendor 054c). Nothing was changed."
        }
    }
    if (($Bind -or $Unbind) -and -not (Test-IsAdministrator)) {
        $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $action = '-Bind'
        if ($Force) { $action += ' -Force' }
        if ($Unbind) { $action = '-Unbind' }
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -BusId {1} {2}' -f $PSCommandPath, $BusId, $action
        $child = Start-Process -FilePath $powerShell -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $arguments
        exit $child.ExitCode
    }
    if (-not ($Bind -or $Detach -or $Unbind)) {
        $wsl = Get-WslExecutable
        Assert-Wsl2Runtime $wsl $Distro $LinuxUser
    }
    foreach ($id in $selected) {
        if ($Bind) {
            $bindArgs = @('bind', '--busid', $id)
            if ($Force) { $bindArgs += '--force' }
            Invoke-CheckedNative $usbipd $bindArgs
        }
        elseif ($Unbind) { Invoke-CheckedNative $usbipd @('unbind', '--busid', $id) }
        elseif ($Detach) { Invoke-CheckedNative $usbipd @('detach', '--busid', $id) }
        else { Invoke-CheckedNative $usbipd @('attach', '--wsl', '--busid', $id) }
    }
    Write-Host 'Selected USB device operation completed. No shutter release was sent.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
