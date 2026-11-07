@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-MultiShutter.ps1" %*
exit /b %ERRORLEVEL%
