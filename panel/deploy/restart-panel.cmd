@echo off
rem Double-click wrapper for restart-panel.ps1; keeps the window open to show the result.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-panel.ps1" %*
pause
