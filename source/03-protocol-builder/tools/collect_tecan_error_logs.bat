@echo off
rem Back-compat wrapper: log collection lives in run_tecan_bundle_setup.bat.
setlocal
set "SETUP_BAT=%~dp0run_tecan_bundle_setup.bat"
if not exist "%SETUP_BAT%" (
    echo ERROR: run_tecan_bundle_setup.bat not found beside this script.
    echo Copy the ready-to-import bundle folder to the instrument PC and run setup from there.
    exit /b 1
)
echo Forwarding to run_tecan_bundle_setup.bat for log collection ...
if "%~1"=="" (
    call "%SETUP_BAT%" --logs-menu
) else (
    call "%SETUP_BAT%" --logs-only %*
)
exit /b %ERRORLEVEL%
