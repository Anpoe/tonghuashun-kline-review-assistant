@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" -c "import PIL, cv2, win32gui, yaml, rapidocr_onnxruntime" >nul 2>&1
    if not errorlevel 1 goto :venv
)

python -c "import PIL, cv2, win32gui, yaml, rapidocr_onnxruntime" >nul 2>&1
if errorlevel 1 (
    echo Runtime dependencies are missing. Preparing the local environment...
    call "%~dp0setup_local.bat" --no-pause
    if errorlevel 1 goto :error
    goto :venv
)

for /f "delims=" %%I in ('python -c "import pathlib, sys; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))"') do set "PYTHONW=%%I"
if not exist "%PYTHONW%" goto :error
start "" /d "%~dp0" "%PYTHONW%" "%~dp0kline_recorder_gui.py"
exit /b 0

:venv
start "" /d "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0kline_recorder_gui.py"
exit /b 0

:error
echo.
echo Unable to start Kline Review Assistant.
if /i not "%~1"=="--no-pause" pause
exit /b 1
