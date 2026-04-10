@echo off
REM Director — double-click or: Launch.cmd [-SkipBrowser] ...
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch.ps1" %*
