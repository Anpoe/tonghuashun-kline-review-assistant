@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 goto :no_python
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv...
    %PYTHON% -m venv ".venv"
    if errorlevel 1 goto :error
)

echo Installing runtime dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Local environment is ready.
if /i not "%~1"=="--no-pause" pause
exit /b 0

:no_python
echo Python 3 was not found. Install Python 3.11 or newer and try again.
goto :error

:error
echo Local environment setup failed.
if /i not "%~1"=="--no-pause" pause
exit /b 1
