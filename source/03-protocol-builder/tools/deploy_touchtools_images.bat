@echo off
rem Back-compat wrapper: TouchTools deploy lives in deploy_touchtools_media.ps1
rem Prefer run_tecan_bundle_setup.bat menu option "Deploy TouchTools media" on instrument PCs.
setlocal EnableExtensions
set "BUNDLE_DIR=%~dp0"
set "PS1=%BUNDLE_DIR%deploy_touchtools_media.ps1"
if not exist "%PS1%" set "PS1=%BUNDLE_DIR%support\deploy_touchtools_media.ps1"
if not exist "%PS1%" (
    echo ERROR: deploy_touchtools_media.ps1 not found beside this script.
    echo Copy the ready-to-import bundle folder to the instrument PC and run setup from there.
    exit /b 1
)
echo Forwarding to deploy_touchtools_media.ps1 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -BundleRoot "%BUNDLE_DIR%" %*
exit /b %ERRORLEVEL%
