@echo off
REM ==========================================================================
REM  Blox Fruits fishing macro
REM
REM  Double-click to start the macro.
REM
REM  RECOMMENDED: right-click this file and choose "Run as administrator".
REM  If Roblox is running as administrator and the macro is not, Windows
REM  silently throws away every click and keypress the macro sends - the
REM  fishing bar drifts to one side and never catches, and F2/F4 do nothing.
REM  Running as administrator lets the macro's input reach the game. The
REM  right-click menu is Windows' own, trusted way to do that.
REM
REM  This file does NOT elevate itself. Some antivirus flags scripts that
REM  silently re-launch themselves with admin rights (a common malware trick),
REM  even when they are harmless - so this uses the right-click menu instead.
REM  Everything here is open source; read every line.
REM ==========================================================================

cd /d "%~dp0"

where py >nul 2>&1 && (py easy_run.py & goto end)
python easy_run.py

:end
echo.
echo (The macro window has opened - you can close this black window.)
pause
