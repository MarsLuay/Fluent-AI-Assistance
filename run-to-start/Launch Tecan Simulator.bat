@echo off
setlocal EnableExtensions
title Tecan Protocol Simulator
cd /d "%~dp0"
if not exist "source\tools\launch_simulator.py" cd ..

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 source\tools\launch_simulator.py %*
  set "EXIT_CODE=%ERRORLEVEL%"
) else (
  python source\tools\launch_simulator.py %*
  set "EXIT_CODE=%ERRORLEVEL%"
)

echo.
if not "%EXIT_CODE%"=="0" (
  echo The simulator launcher exited with an error. Check the message above.
)
echo.
pause
exit /b %EXIT_CODE%
