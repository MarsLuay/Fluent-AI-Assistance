@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Deploy TouchTools media after importing generated_project.zeia.
rem Run from the instrument PC. Layout: this .bat lives at the bundle root next to media\.

set "BUNDLE_DIR=%~dp0"
set "LOG=%BUNDLE_DIR%deploy_touchtools_images.log"
set "SRC=%BUNDLE_DIR%media\processed"
set "IMAGES_ROOT=%ProgramData%\Tecan\VisionX\TouchToolsData\Images"
set "MEDIA_SUBFOLDER="
set "DEPLOY_CFG=%BUNDLE_DIR%source\touchtools_deploy.json"
set "ERR=0"
set "COPIED=0"
set "SKIPPED=0"
set "FAILED=0"

>"%LOG%" echo [%DATE% %TIME%] deploy_touchtools_images.bat started

call :write_log "==================================================="
call :write_log "TouchTools media deploy"
call :write_log "Started: %DATE% %TIME%"
call :write_log "Bundle dir: %BUNDLE_DIR%"
call :write_log "Log file: %LOG%"
call :write_log "Tip: close FluentControl Script Editor preview windows before deploy."

if exist "%DEPLOY_CFG%" (
    for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$c=Get-Content -Raw -LiteralPath '%DEPLOY_CFG%' | ConvertFrom-Json; if ($c.media_subfolder) { Write-Output $c.media_subfolder }"`) do set "MEDIA_SUBFOLDER=%%V"
)
if not defined MEDIA_SUBFOLDER (
    if exist "%BUNDLE_DIR%source\metadata.json" (
        for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "$c=Get-Content -Raw -LiteralPath '%BUNDLE_DIR%source\metadata.json' | ConvertFrom-Json; if ($c.script_name) { Write-Output ($c.script_name + '_media') }"`) do set "MEDIA_SUBFOLDER=%%V"
    )
)
if not defined MEDIA_SUBFOLDER set "MEDIA_SUBFOLDER=script_media"
set "DEST=%IMAGES_ROOT%\%MEDIA_SUBFOLDER%"
tasklist /FI "IMAGENAME eq FluentControl.exe" 2>nul | find /I "FluentControl.exe" >nul
if not errorlevel 1 (
    call :write_log "WARN: FluentControl.exe is running. Close preview windows or exit FluentControl if copies fail."
)
tasklist /FI "IMAGENAME eq VisionX.exe" 2>nul | find /I "VisionX.exe" >nul
if not errorlevel 1 (
    call :write_log "WARN: VisionX.exe is running. TouchTools may lock files under %DEST%."
)

if not exist "%SRC%\" (
    set "SRC=%BUNDLE_DIR%media"
    call :write_log "WARN: media\processed not found; trying flat media\: !SRC!"
)
if not exist "%SRC%\" (
    set "SRC=%BUNDLE_DIR%source\media"
    call :write_log "WARN: bundle-root media not found; trying source\media: !SRC!"
)

call :write_log "Source: !SRC!"
call :write_log "Images root: %IMAGES_ROOT%"
call :write_log "Media subfolder: %MEDIA_SUBFOLDER%"
call :write_log "Target: %DEST%"

if not exist "!SRC!\" (
    call :write_log "ERROR: media folder not found."
    call :write_log "       Tried: %BUNDLE_DIR%media\processed"
    call :write_log "       Tried: %BUNDLE_DIR%media"
    call :write_log "       Tried: %BUNDLE_DIR%source\media"
    call :write_log "       Re-run generate packaging or copy the full ready-to-import bundle, then re-run."
    set "ERR=1"
    goto :finish
)

if not exist "%DEST%\" (
    call :write_log "Creating TouchTools media folder..."
    mkdir "%DEST%" 2>nul
    if errorlevel 1 (
        call :write_log "ERROR: Could not create %DEST%"
        call :write_log "       Run as Administrator or check ProgramData permissions."
        set "ERR=1"
        goto :finish
    )
)

if exist "!SRC!\preview__png.png" (
    call :write_log "Mode: TouchTools image format test (fixed preview filenames)"
    call :copy_one "preview__png.png"
    call :copy_one "preview__jpg.jpg"
    call :copy_one "preview__jpeg.jpeg"
    call :copy_one "preview__bmp.bmp"
    call :copy_one "preview__tif.tif"
    call :copy_one "preview__tiff.tiff"
    call :copy_one "preview__webp.webp"
    call :copy_one "preview__gif_static.gif"
    call :copy_one "preview__gif_anim.gif"
    call :copy_one "preview__mp4.mp4"
) else (
    call :write_log "Mode: verification / prompt media slots (copy all media files except README.md)"
    for /f "delims=" %%F in ('dir /b /a-d "!SRC!" 2^>nul') do (
        if /I not "%%F"=="README.md" call :copy_one "%%F"
    )
)

if "!COPIED!"=="0" if "!SKIPPED!"=="0" (
    if "!FAILED!"=="0" (
        call :write_log "ERROR: No media files were copied from !SRC!"
        set "ERR=1"
    )
)

:finish
if "!FAILED!"=="0" (
    if not "!COPIED!"=="0" set "ERR=0"
    if not "!SKIPPED!"=="0" set "ERR=0"
)
echo.
if "!ERR!"=="1" (
    call :write_log "Deploy finished with errors. Copied: !COPIED!, skipped (already current): !SKIPPED!, failed: !FAILED!."
    call :write_log "Log: %LOG%"
    call :write_log "If a file was locked, close FluentControl/VisionX, delete the locked file in %DEST%, then re-run."
    echo Deploy FAILED. Log: %LOG%
    echo.
    pause
    exit /b 1
)

call :write_log "Deploy complete. Copied: !COPIED!, skipped (already current): !SKIPPED!."
call :write_log "Target: %DEST%"
call :write_log "Next: run initialization worktable, then Preview RUP Standard media prompts (or plain User Prompt for text-only steps) in Script Editor."
call :write_log "Finished OK: %DATE% %TIME%"
echo Deploy complete. Copied: !COPIED! file(s), skipped: !SKIPPED!. Log: %LOG%
echo.
pause
exit /b 0

:copy_one
set "CURRENT_FILE=%~1"
set "FSZ="
if not exist "!SRC!\!CURRENT_FILE!" (
    call :write_log "ERROR MISSING  !CURRENT_FILE!  in !SRC!"
    set /A FAILED+=1
    set "ERR=1"
    exit /b 0
)
if exist "%DEST%\!CURRENT_FILE!" (
    for %%A in ("!SRC!\!CURRENT_FILE!") do set "SRC_SIZE=%%~zA"
    for %%B in ("%DEST%\!CURRENT_FILE!") do set "DEST_SIZE=%%~zB"
    if "!SRC_SIZE!"=="!DEST_SIZE!" (
        fc /b "!SRC!\!CURRENT_FILE!" "%DEST%\!CURRENT_FILE!" >nul 2>&1
        if not errorlevel 1 (
            set /A SKIPPED+=1
            call :write_log "SKIP     !CURRENT_FILE!  already matches destination (!DEST_SIZE! bytes)"
            exit /b 0
        )
    )
)
set "COPY_OK=0"
set "TMP=%TEMP%\tt_deploy_%RANDOM%_!CURRENT_FILE!"
for /L %%R in (1,1,12) do if "!COPY_OK!"=="0" (
    if exist "%DEST%\!CURRENT_FILE!" (
        del /F /Q "%DEST%\!CURRENT_FILE!" >nul 2>&1
        if exist "%DEST%\!CURRENT_FILE!" (
            ren "%DEST%\!CURRENT_FILE!" "!CURRENT_FILE!.deploy_bak" >nul 2>&1
        )
    )
    del "!TMP!" >nul 2>&1
    copy /B /Y "!SRC!\!CURRENT_FILE!" "!TMP!" >nul 2>&1
    if not errorlevel 1 (
        move /Y "!TMP!" "%DEST%\!CURRENT_FILE!" >nul 2>&1
        if not errorlevel 1 set "COPY_OK=1"
    )
    if "!COPY_OK!"=="0" (
        robocopy "!SRC!" "%DEST%" "!CURRENT_FILE!" /R:2 /W:1 /IS /IT /NJH /NJS /NDL /NC /NS >nul 2>&1
        if not errorlevel 8 if exist "%DEST%\!CURRENT_FILE!" set "COPY_OK=1"
    )
    if "!COPY_OK!"=="0" (
        set "TT_SRC=!SRC!\!CURRENT_FILE!"
        set "TT_DEST=%DEST%\!CURRENT_FILE!"
        powershell -NoProfile -Command "Copy-Item -LiteralPath $env:TT_SRC -Destination $env:TT_DEST -Force" >nul 2>&1
        if not errorlevel 1 if exist "%DEST%\!CURRENT_FILE!" set "COPY_OK=1"
    )
    if "!COPY_OK!"=="0" ping -n %%R 127.0.0.1 >nul
)
del "!TMP!" >nul 2>&1
if "!COPY_OK!"=="0" (
    call :write_log "ERROR FAILED   !CURRENT_FILE!  could not copy to %DEST% - file locked or permission denied"
    call :write_log "         Close FluentControl/VisionX preview, delete or rename %DEST%\!CURRENT_FILE! manually, re-run this bat."
    call :write_log "         If rename works but delete does not, delete any *.deploy_bak files in %DEST% after closing FluentControl."
    set /A FAILED+=1
    set "ERR=1"
    exit /b 0
)
if exist "%DEST%\!CURRENT_FILE!" for %%A in ("%DEST%\!CURRENT_FILE!") do set "FSZ=%%~zA"
set /A COPIED+=1
call :write_log "OK       !CURRENT_FILE!  size=!FSZ! bytes"
exit /b 0

:write_log
set "MSG=%~1"
echo !MSG!
>>"%LOG%" echo [%DATE% %TIME%] !MSG!
exit /b 0
