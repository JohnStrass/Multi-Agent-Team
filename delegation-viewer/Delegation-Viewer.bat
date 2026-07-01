@echo off
title Delegation Viewer Launcher
cd /d "C:\Claude-LLM-Projects\delegation-viewer"
rem start the server only if nothing is already listening on 8809
netstat -ano | findstr ":8809" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto open
start "Delegation Viewer Server" /min py server.py
timeout /t 2 /nobreak >nul
:open
start "" "http://127.0.0.1:8809"
