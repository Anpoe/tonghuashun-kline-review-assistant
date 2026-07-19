@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_release.ps1"
if errorlevel 1 goto :error
echo.
echo Release build complete. See:
echo %CD%\release
pause
exit /b 0

:error
echo.
echo Build failed. See the messages above.
pause
exit /b 1
