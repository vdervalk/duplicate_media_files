@echo off
REM Start de app rechtstreeks met Python (zonder .exe te bouwen).
setlocal
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0run.py"
) else (
    start "" pythonw "%~dp0run.py"
)
