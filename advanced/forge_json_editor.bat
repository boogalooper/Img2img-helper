@echo off
setlocal
cd /d "%~dp0"
py "%~dp0forge_json_editor.py" %*
if errorlevel 1 (
    echo.
    echo Failed to start Forge JSON Editor.
    echo Make sure Python is installed and "py" launcher is available.
    pause
)
