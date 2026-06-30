@echo off
title Echo-Win Launcher
cd /d "%~dp0"
rem start the server only if nothing is already listening on 8808
netstat -ano | findstr ":8808" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto open
start "Echo-Win Server" /min py server.py
timeout /t 2 /nobreak >nul
:open
start "" "http://127.0.0.1:8808"
