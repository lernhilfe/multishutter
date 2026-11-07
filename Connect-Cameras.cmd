@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Connect-Cameras.ps1" %*
exit /b %ERRORLEVEL%
