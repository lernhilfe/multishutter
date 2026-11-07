[CmdletBinding()]
param([string]$Distro = 'Ubuntu-24.04')
$ErrorActionPreference = 'Stop'
& wsl.exe -d $Distro -u root --exec apt-get update
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& wsl.exe -d $Distro -u root --exec apt-get install -y --no-install-recommends docker.io docker-compose-v2 docker-buildx
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& wsl.exe -d $Distro -u root --exec systemctl start docker
exit $LASTEXITCODE
