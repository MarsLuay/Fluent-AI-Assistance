@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Tecan Protocol Simulator
cd /d "%~dp0"
if not exist "source\tools\simulator\launch_simulator.py" cd ..

set "LAUNCHER=source\tools\simulator\launch_simulator.py"
if not exist "%LAUNCHER%" (
  echo ERROR: Could not find !LAUNCHER!
  echo Run this from the Fluent-AI-Assistance repo ^(run-to-start or repo root^).
  echo.
  pause
  exit /b 1
)

set "EXIT_CODE=1"
where py >nul 2>nul
if !ERRORLEVEL!==0 (
  py -3 "!LAUNCHER!" %*
  set "EXIT_CODE=!ERRORLEVEL!"
  goto :after_launch
)

where python >nul 2>nul
if !ERRORLEVEL!==0 (
  python "!LAUNCHER!" %*
  set "EXIT_CODE=!ERRORLEVEL!"
  goto :after_launch
)

echo ERROR: Python 3 was not found on PATH.
echo Install Python 3 from https://www.python.org/downloads/ ^(check "Add python.exe to PATH"^),
echo or from the Microsoft Store, then re-run this launcher.
echo.
pause
exit /b 1

:after_launch
echo.
if not "!EXIT_CODE!"=="0" (
  echo The simulator launcher exited with an error. Check the message above.
)
echo.
pause
exit /b !EXIT_CODE!
