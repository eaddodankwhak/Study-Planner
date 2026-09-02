@echo off
REM Study Planner launcher - run the app quickly and open it in the browser.
setlocal

REM Move into the directory containing this script.
cd /d "%~dp0"

REM app.py uses relative paths (../Frontend), so it must run from the Backend folder.
if not exist "Backend\app.py" (
    echo Could not find Backend\app.py. Make sure run.bat is in the project root.
    pause
    exit /b 1
)

REM Use the project virtual environment if it exists, otherwise system Python.
if exist "Backend\.venv\Scripts\python.exe" (
    set "PY=%~dp0Backend\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM Open the app in the default browser shortly after startup.
start "" http://127.0.0.1:5000/

REM Run the Flask app from the Backend directory.
cd /d "%~dp0Backend"
"%PY%" app.py

endlocal
