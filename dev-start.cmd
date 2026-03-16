@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoExit -Command "cd '%~dp0'; & '.\.venv\Scripts\Activate.ps1'; adb devices; adb reverse tcp:8000 tcp:8000; adb reverse --list; & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level debug"
