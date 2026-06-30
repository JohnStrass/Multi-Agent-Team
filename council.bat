@echo off
REM Double-click this to start the council. It runs from this folder so your
REM saved transcripts (/save) land right here next to council.py.
cd /d "%~dp0"
".venv\Scripts\python.exe" council.py %*
echo.
pause
