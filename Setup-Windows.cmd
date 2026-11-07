@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Windows.ps1" %*
set "multishutter_status=%ERRORLEVEL%"
pause
exit /b %multishutter_status%
