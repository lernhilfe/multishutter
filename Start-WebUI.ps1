[CmdletBinding()]
param([switch]$Demo, [switch]$Stop, [switch]$NoBrowser, [int]$LocalPort = 0, [string]$Distro = 'Ubuntu-24.04')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\windows-common.ps1')
try {
    $wsl = Get-WslExecutable
    $mode = 'up'
    if ($Demo) { $mode = 'demo' }
    if ($Stop) { $mode = 'down' }
    if ($Stop -and $Demo) { $mode = 'demo-down' }
    & $wsl -d $Distro -u root --exec systemctl start docker
    if ($LASTEXITCODE -ne 0) { throw 'Docker konnte in WSL nicht gestartet werden. Siehe DOCKER.md.' }
    # Use a targeted administrative Docker invocation without granting persistent group access.
    & $wsl -d $Distro -u root --cd $PSScriptRoot --exec bash scripts/compose-start.sh $mode
    if ($LASTEXITCODE -ne 0) { throw 'Compose-Start fehlgeschlagen. Siehe DOCKER.md.' }
    $port = 8000
    if ($Demo) { $port = 8001 }
    if ($LocalPort -eq 0) { $LocalPort = 8787; if ($Demo) { $LocalPort = 8788 } }
    if ($LocalPort -lt 1024 -or $LocalPort -gt 65535) { throw 'LocalPort muss zwischen 1024 und 65535 liegen.' }
    $proxyState = Join-Path $PSScriptRoot ".setup\web-proxy-$port.json"
    if (Test-Path -LiteralPath $proxyState) {
        $oldProxy = Get-Content -LiteralPath $proxyState -Raw | ConvertFrom-Json
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($oldProxy.processId)" -ErrorAction SilentlyContinue
        if ($oldProcess -and $oldProcess.Name -eq 'node.exe' -and
            $oldProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'scripts\wsl-proxy.mjs'))) {
            Stop-Process -Id $oldProxy.processId -ErrorAction SilentlyContinue
            Wait-Process -Id $oldProxy.processId -Timeout 5 -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $proxyState
    }
    if (-not $Stop) {
        $addresses = & $wsl -d $Distro -u root --exec hostname -I
        $wslAddress = (($addresses -join '').Trim() -split '\s+')[0]
        $url = "http://${wslAddress}:$port"
        $node = Get-Command node.exe -ErrorAction SilentlyContinue
        if ($node) {
            New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot '.setup') -Force | Out-Null
            $proxyArguments = '"{0}" {1} {2} {3}' -f (Join-Path $PSScriptRoot 'scripts\wsl-proxy.mjs'), $wslAddress, $port, $LocalPort
            $proxy = Start-Process -FilePath $node.Source -ArgumentList $proxyArguments -WindowStyle Hidden -PassThru
            @{processId=$proxy.Id; target=$wslAddress; port=$port; localPort=$LocalPort} | ConvertTo-Json | Set-Content -LiteralPath $proxyState
            $check = $null
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            do {
                try {
                    $check = Invoke-RestMethod "http://127.0.0.1:$LocalPort/api/health" -TimeoutSec 2
                    if ($check.app -eq 'multishutter' -and -not $proxy.HasExited) { break }
                } catch { }
                Start-Sleep -Milliseconds 500
                $proxy.Refresh()
            } while ([DateTime]::UtcNow -lt $deadline -and -not $proxy.HasExited)
            if (-not $proxy.HasExited -and $check -and $check.app -eq 'multishutter') {
                $url = "http://localhost:$LocalPort"
            } else {
                Write-Warning "Lokale Weiterleitung nicht verfügbar; verwende $url"
            }
        }
        Write-Host "MultiShutter: $url"
        if (-not $NoBrowser) { Start-Process $url }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
