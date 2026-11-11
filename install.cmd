@echo off
rem Double-click installer: runs setup.ps1 from this folder, then waits so the result stays visible.
rem Execution policy is bypassed for this one PowerShell process only; system settings are not changed.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "SETUP_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %SETUP_EXIT%
