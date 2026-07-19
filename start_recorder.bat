@echo off
cd /d "%~dp0"
if exist "dist\K线复盘助手\K线复盘助手.exe" (
    start "" "dist\K线复盘助手\K线复盘助手.exe"
) else (
    start "" pythonw kline_recorder_gui.py
)
