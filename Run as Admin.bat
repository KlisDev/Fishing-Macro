@echo off
REM ==========================================================================
REM  Blox Fruits fishing macro - run as administrator
REM
REM  Just double-click this file. It asks Windows for admin rights (you'll get
REM  a "Do you want to allow..." popup - click Yes) and then starts the macro.
REM
REM  WHY ADMIN: if Roblox is running as administrator and the macro is not,
REM  Windows silently throws away every click and keypress the macro sends -
REM  the fishing bar just drifts to one side and never catches, and F2/F4 do
REM  nothing. Running the macro as admin too lets its input reach the game.
REM ==========================================================================

REM Already elevated? Then just run. Otherwise re-launch this file elevated.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

REM Prefer the launcher that installs dependencies; fall back to python.
where py >nul 2>&1 && (py easy_run.py & goto done)
python easy_run.py

:done
echo.
echo (the macro window has opened - you can close this black window)
pause
