@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Support utility for a published Tecan ready-to-import bundle.
rem This is the only BAT published at the bundle root.

set "BUNDLE_DIR=%~dp0"
set "BUNDLE_ARG=%BUNDLE_DIR%"
if "%BUNDLE_ARG:~-1%"=="\" set "BUNDLE_ARG=%BUNDLE_ARG:~0,-1%"
set "TEMP_DIR=%BUNDLE_DIR%temp_files\"
set "TEMP_ARG=%TEMP_DIR%"
if "%TEMP_ARG:~-1%"=="\" set "TEMP_ARG=%TEMP_ARG:~0,-1%"
set "SUPPORT_DIR=%BUNDLE_DIR%source\"
if not exist "%SUPPORT_DIR%collect_tecan_diagnostic_bundle.ps1" set "SUPPORT_DIR=%BUNDLE_DIR%support\"
if not exist "%SUPPORT_DIR%collect_tecan_diagnostic_bundle.ps1" set "SUPPORT_DIR=%BUNDLE_DIR%"
if not exist "%TEMP_DIR%" mkdir "%TEMP_DIR%" >nul 2>&1
set "LOG=%TEMP_DIR%tecan_bundle_setup.log"
set "SETTINGS_FILE=%TEMP_DIR%run_tecan_bundle_setup.settings.cmd"
set "NO_PAUSE=0"
set "RUN_LOGS=0"
set "RUN_COLLECT_INSTRUMENT=0"
set "RUN_COLLECT_METHOD_SOURCE=0"
set "RUN_INSTALL_INSTRUMENT=0"
set "RUN_INSTALL_EXTERNAL=0"
set "RUN_DEPLOY_TOUCHTOOLS=0"
set "LOG_PROFILE=everything"
set "LOG_PROFILE_LABEL=Everything"
set "LOG_LOOKBACK_DAYS=1"
set "LIKELY_CAUSE_MAX_RECORDS=200"
set "WINDOWS_EVENT_MAX_EVENTS=2000"
set "SHOW_LOG_MENU=0"
set "SETUP_ERROR=0"
set "STALL_WATCHDOG_PID="
set "TECAN_SETUP_HEARTBEAT="
set "TECAN_SETUP_STALL_ERROR="
set "TECAN_SETUP_STALL_COMPLETE="
set "TECAN_SETUP_STALL_PID="

call :load_settings

>"%LOG%" echo [%DATE% %TIME%] run_tecan_bundle_setup.bat started

if "%~1"=="" goto :menu

:parse_args
if "%~1"=="" goto :run_selected
if /I "%~1"=="--logs-only" goto :arg_logs
if /I "%~1"=="--logs-menu" goto :arg_logs_menu
if /I "%~1"=="--log-profile" goto :arg_log_profile
if /I "%~1"=="--logs-everything" goto :arg_logs_everything
if /I "%~1"=="--logs-script-errors" goto :arg_logs_script_errors
if /I "%~1"=="--logs-program-crash" goto :arg_logs_program_crash
if /I "%~1"=="--logs-import-errors" goto :arg_logs_import_errors
if /I "%~1"=="--collect-instrument" goto :arg_collect_instrument
if /I "%~1"=="--collect-method-source" goto :arg_collect_method_source
if /I "%~1"=="--install-instrument" goto :arg_install_instrument
if /I "%~1"=="--install-external-files" goto :arg_install_external
if /I "%~1"=="--deploy-touchtools" goto :arg_deploy_touchtools
if /I "%~1"=="--no-pause" goto :arg_no_pause
if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage
echo ERROR: Unknown option %~1
goto :usage_error

:arg_logs
set "RUN_LOGS=1"
shift
goto :parse_args

:arg_logs_menu
set "SHOW_LOG_MENU=1"
shift
goto :parse_args

:arg_log_profile
set "RUN_LOGS=1"
shift
if "%~1"=="" (
    echo ERROR: --log-profile requires one of: everything, script-errors, program-crash, import-errors
    goto :usage_error
)
call :set_log_profile "%~1"
if errorlevel 1 goto :usage_error
shift
goto :parse_args

:arg_logs_everything
set "RUN_LOGS=1"
call :set_log_profile "everything"
shift
goto :parse_args

:arg_logs_script_errors
set "RUN_LOGS=1"
call :set_log_profile "script-errors"
shift
goto :parse_args

:arg_logs_program_crash
set "RUN_LOGS=1"
call :set_log_profile "program-crash"
shift
goto :parse_args

:arg_logs_import_errors
set "RUN_LOGS=1"
call :set_log_profile "import-errors"
shift
goto :parse_args

:arg_collect_instrument
set "RUN_COLLECT_INSTRUMENT=1"
shift
goto :parse_args

:arg_collect_method_source
set "RUN_COLLECT_METHOD_SOURCE=1"
shift
goto :parse_args

:arg_install_instrument
set "RUN_INSTALL_INSTRUMENT=1"
set "RUN_INSTALL_EXTERNAL=1"
shift
goto :parse_args

:arg_install_external
set "RUN_INSTALL_EXTERNAL=1"
shift
goto :parse_args

:arg_deploy_touchtools
set "RUN_DEPLOY_TOUCHTOOLS=1"
shift
goto :parse_args

:arg_no_pause
set "NO_PAUSE=1"
shift
goto :parse_args

:menu
set "RUN_LOGS=0"
set "RUN_COLLECT_INSTRUMENT=0"
set "RUN_COLLECT_METHOD_SOURCE=0"
set "RUN_INSTALL_INSTRUMENT=0"
set "RUN_INSTALL_EXTERNAL=0"
set "RUN_DEPLOY_TOUCHTOOLS=0"
set "SHOW_LOG_MENU=0"
echo.
echo Tecan support utility
echo =====================
echo 1. Collect Logs
echo 2. Collect/Install Drivers and Configs
echo 3. Deploy TouchTools media
echo 4. Settings
echo 5. Exit
echo.
choice /C 12345 /N /M "Select an option [1-5]: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto :settings_menu
if errorlevel 3 (
    set "RUN_DEPLOY_TOUCHTOOLS=1"
    goto :run_selected
)
if errorlevel 2 goto :driver_config_menu
goto :log_profile_menu

:log_profile_menu
set "SHOW_LOG_MENU=0"
echo.
echo Choose the error type you want logs for.
echo ========================================
echo 1. Everything
echo 2. In-Script errors
echo 3. Tecan Program Crash
echo 4. Import errors
echo 5. Back
echo.
choice /C 12345 /N /M "Select a diagnostic log package [1-5]: "
if errorlevel 5 goto :menu
set "RUN_LOGS=1"
if errorlevel 4 (
    call :set_log_profile "import-errors"
    goto :run_selected
)
if errorlevel 3 (
    call :set_log_profile "program-crash"
    goto :run_selected
)
if errorlevel 2 (
    call :set_log_profile "script-errors"
    goto :run_selected
)
call :set_log_profile "everything"
goto :run_selected

:driver_config_menu
set "SHOW_LOG_MENU=0"
echo.
echo Driver/config package
echo =====================
echo 1. Collect instrument driver/config snapshot into this bundle
echo 2. Install instrument driver/config snapshot and staged external files
echo 3. Collect Tecan method source for inspection
echo 4. Back
echo.
choice /C 1234 /N /M "Select a driver/config option [1-4]: "
if errorlevel 4 goto :menu
if errorlevel 3 (
    set "RUN_COLLECT_METHOD_SOURCE=1"
    goto :run_selected
)
if errorlevel 2 (
    set "RUN_INSTALL_INSTRUMENT=1"
    set "RUN_INSTALL_EXTERNAL=1"
    goto :run_selected
)
set "RUN_COLLECT_INSTRUMENT=1"
goto :run_selected

:settings_menu
set "SHOW_LOG_MENU=0"
echo.
echo Settings
echo ========
echo Current settings for this BAT:
echo   Log lookback days: %LOG_LOOKBACK_DAYS%
echo   Likely-cause max log records: %LIKELY_CAUSE_MAX_RECORDS%
echo   Windows event max records: %WINDOWS_EVENT_MAX_EVENTS%
echo   Settings file: %SETTINGS_FILE%
echo.
echo 1. Change log lookback days
echo 2. Change likely-cause max log records
echo 3. Change Windows event max records
echo 4. Reset settings to defaults
echo 5. Back
echo.
choice /C 12345 /N /M "Select a settings option [1-5]: "
if errorlevel 5 goto :menu
if errorlevel 4 (
    call :reset_settings
    goto :settings_menu
)
if errorlevel 3 (
    call :prompt_numeric_setting "WINDOWS_EVENT_MAX_EVENTS" "Windows event max records" 100 10000
    goto :settings_menu
)
if errorlevel 2 (
    call :prompt_numeric_setting "LIKELY_CAUSE_MAX_RECORDS" "Likely-cause max log records" 20 5000
    goto :settings_menu
)
call :prompt_numeric_setting "LOG_LOOKBACK_DAYS" "Log lookback days" 1 90
goto :settings_menu

:run_selected
if "%SHOW_LOG_MENU%"=="1" goto :log_profile_menu
if "%RUN_LOGS%%RUN_COLLECT_INSTRUMENT%%RUN_COLLECT_METHOD_SOURCE%%RUN_INSTALL_INSTRUMENT%%RUN_INSTALL_EXTERNAL%%RUN_DEPLOY_TOUCHTOOLS%"=="000000" goto :menu
call :setup_log "==================================================="
call :setup_log "Tecan support utility"
echo Bundle: %BUNDLE_ARG%
>>"%LOG%" echo [%DATE% %TIME%] Bundle: %BUNDLE_ARG%

if "%RUN_LOGS%"=="1" (
    call :phase_collect_logs
    if errorlevel 1 set "SETUP_ERROR=1"
)
if "%RUN_COLLECT_INSTRUMENT%"=="1" (
    call :phase_collect_instrument
    if errorlevel 1 set "SETUP_ERROR=1"
)
if "%RUN_COLLECT_METHOD_SOURCE%"=="1" (
    call :phase_collect_method_source
    if errorlevel 1 set "SETUP_ERROR=1"
)
if "%RUN_INSTALL_EXTERNAL%"=="1" (
    call :phase_install_external
    if errorlevel 1 set "SETUP_ERROR=1"
)
if "%RUN_INSTALL_INSTRUMENT%"=="1" (
    call :phase_install_instrument
    if errorlevel 1 set "SETUP_ERROR=1"
)
if "%RUN_DEPLOY_TOUCHTOOLS%"=="1" (
    call :phase_deploy_touchtools
    if errorlevel 1 set "SETUP_ERROR=1"
)
goto :finish

:phase_collect_logs
call :setup_log "Phase: collect diagnostic logs (%LOG_PROFILE_LABEL%)"
call :setup_log "Settings: log lookback days=%LOG_LOOKBACK_DAYS%, likely-cause max records=%LIKELY_CAUSE_MAX_RECORDS%, Windows event max records=%WINDOWS_EVENT_MAX_EVENTS%"
call :setup_log "Progress bars update while scanning/copying. Quiet stretches usually mean file enumeration, not a hang."
set "SETUP_LOG_OUTPUT=%TEMP_ARG%"
set "SETUP_LOG_PROFILE=%LOG_PROFILE%"
set "SETUP_LOG_PROFILE_LABEL=%LOG_PROFILE_LABEL%"
set "SETUP_LOG_DAYS=%LOG_LOOKBACK_DAYS%"
set "SETUP_LIKELY_CAUSE_MAX_RECORDS=%LIKELY_CAUSE_MAX_RECORDS%"
set "SETUP_WINDOWS_EVENT_MAX_EVENTS=%WINDOWS_EVENT_MAX_EVENTS%"
set "SETUP_LOG_SCRIPT=%SUPPORT_DIR%collect_tecan_diagnostic_bundle.ps1"
set "FLUENTCONTROL_INFOPAD_ARG="
if /I "%LOG_PROFILE%"=="script-errors" set "FLUENTCONTROL_INFOPAD_ARG=-CaptureFluentControlInfopad"
if /I "%LOG_PROFILE%"=="everything" set "FLUENTCONTROL_INFOPAD_ARG=-CaptureFluentControlInfopad"
if not exist "%SETUP_LOG_SCRIPT%" (
    call :setup_log "ERROR source\collect_tecan_diagnostic_bundle.ps1 is required."
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP_LOG_SCRIPT%" -Profile "%LOG_PROFILE%" -OutputRoot "%TEMP_ARG%" -BundleRoot "%BUNDLE_ARG%" -SinceDays %LOG_LOOKBACK_DAYS% -LikelyCauseMaxRecords %LIKELY_CAUSE_MAX_RECORDS% -EventLogMaxEvents %WINDOWS_EVENT_MAX_EVENTS% %FLUENTCONTROL_INFOPAD_ARG%
if errorlevel 1 (
    call :setup_log "ERROR diagnostic log collection failed."
    exit /b 1
)
call :setup_log "Diagnostic log collection complete."
exit /b 0

:phase_collect_instrument
call :setup_log "Phase: collect instrument driver/config snapshot"
call :start_stall_watchdog "collect-instrument" 120 45
set "SNAPSHOT=%TEMP_DIR%instrument_snapshot"
call :remove_tree_progress "%SNAPSHOT%" "Clearing previous instrument snapshot"
mkdir "%SNAPSHOT%" >nul 2>&1
call :mirror_collect "%ProgramData%\Tecan\VisionX\Config" "%SNAPSHOT%\VisionX_Config" "1/5 VisionX Config"
call :mirror_collect "%ProgramData%\Tecan\VisionX\InstrumentConfigurations" "%SNAPSHOT%\VisionX_InstrumentConfigurations" "2/5 InstrumentConfigurations"
call :mirror_collect "%ProgramData%\Tecan\VisionX\InstrumentInformation" "%SNAPSHOT%\VisionX_InstrumentInformation" "3/5 InstrumentInformation"
call :mirror_collect "%ProgramData%\Tecan\VisionX\MapDataBase" "%SNAPSHOT%\VisionX_MapDataBase" "4/5 MapDataBase"
call :mirror_collect "%ProgramFiles%\Tecan\FluentControl\Drivers" "%SNAPSHOT%\FluentControl_Drivers" "5/5 FluentControl Drivers"
call :stop_stall_watchdog
call :setup_log "Instrument snapshot collection complete: %SNAPSHOT%"
exit /b 0

:phase_collect_method_source
call :setup_log "Phase: collect Tecan method source for inspection"
call :setup_log "Large VisionX databases can take several minutes."
call :start_stall_watchdog "collect-method-source" 120 45
set "METHOD_SOURCE=%TEMP_DIR%tecan_method_source"
call :setup_log "First clearing any previous method-source copy. Progress bar appears next."
call :touch_progress_heartbeat "clearing-previous-method-source"
call :remove_tree_progress "%METHOD_SOURCE%" "Clearing previous method-source copy"
mkdir "%METHOD_SOURCE%" >nul 2>&1
call :setup_log "Now copying VisionX databases. Progress bar appears for each step."
call :mirror_collect "%ProgramData%\Tecan\VisionX\DataBase\UserSpecific" "%METHOD_SOURCE%\DataBase_UserSpecific" "1/5 DataBase UserSpecific"
call :mirror_collect "%ProgramData%\Tecan\VisionX\DataBase\SystemSpecific" "%METHOD_SOURCE%\DataBase_SystemSpecific" "2/5 DataBase SystemSpecific"
call :copy_file_collect "%ProgramData%\Tecan\VisionX\DataBase\install_index.db" "%METHOD_SOURCE%\install_index.db"
call :setup_log "3/5 Collected install_index.db"
call :mirror_collect "%ProgramData%\Tecan\VisionX\Worklists" "%METHOD_SOURCE%\VisionX_Worklists" "4/5 VisionX Worklists"
call :mirror_collect "%ProgramData%\Tecan\VisionX\ExternalLinks" "%METHOD_SOURCE%\VisionX_ExternalLinks" "5/5 VisionX ExternalLinks"
(
    echo Tecan method source collection - read-only copy.
    echo Source: %ProgramData%\Tecan\VisionX\DataBase\UserSpecific
    echo Source: %ProgramData%\Tecan\VisionX\DataBase\SystemSpecific
    echo Source: %ProgramData%\Tecan\VisionX\Worklists
    echo Source: %ProgramData%\Tecan\VisionX\ExternalLinks
) > "%METHOD_SOURCE%\COLLECTION_README.txt"
call :stop_stall_watchdog
call :setup_log "Tecan method source collection complete: %METHOD_SOURCE%"
exit /b 0

:phase_install_instrument
call :setup_log "Phase: install instrument driver/config snapshot"
set "ELEVATE_ARGS=--install-instrument"
if "%NO_PAUSE%"=="1" set "ELEVATE_ARGS=%ELEVATE_ARGS% --no-pause"
call :require_admin
if errorlevel 1 (
    call :setup_log "ERROR Administrator privileges are required for instrument install."
    exit /b 1
)
set "SNAPSHOT=%TEMP_DIR%instrument_snapshot"
if not exist "%SNAPSHOT%\" (
    call :setup_log "ERROR instrument_snapshot is missing."
    exit /b 1
)
call :mirror_install "%SNAPSHOT%\VisionX_Config" "%ProgramData%\Tecan\VisionX\Config" "1/5 Install VisionX Config"
if errorlevel 1 exit /b 1
call :mirror_install "%SNAPSHOT%\VisionX_InstrumentConfigurations" "%ProgramData%\Tecan\VisionX\InstrumentConfigurations" "2/5 Install InstrumentConfigurations"
if errorlevel 1 exit /b 1
call :mirror_install "%SNAPSHOT%\VisionX_InstrumentInformation" "%ProgramData%\Tecan\VisionX\InstrumentInformation" "3/5 Install InstrumentInformation"
if errorlevel 1 exit /b 1
call :mirror_install "%SNAPSHOT%\VisionX_MapDataBase" "%ProgramData%\Tecan\VisionX\MapDataBase" "4/5 Install MapDataBase"
if errorlevel 1 exit /b 1
call :mirror_install "%SNAPSHOT%\FluentControl_Drivers" "%ProgramFiles%\Tecan\FluentControl\Drivers" "5/5 Install FluentControl Drivers"
if errorlevel 1 exit /b 1
call :setup_log "Instrument driver/config installation complete."
exit /b 0

:phase_install_external
call :setup_log "Phase: install staged external files"
if "%RUN_INSTALL_INSTRUMENT%"=="1" (
    set "ELEVATE_ARGS=--install-instrument"
) else (
    set "ELEVATE_ARGS=--install-external-files"
)
if "%NO_PAUSE%"=="1" set "ELEVATE_ARGS=%ELEVATE_ARGS% --no-pause"
call :require_admin
if errorlevel 1 (
    call :setup_log "ERROR Administrator privileges are required for external file installation."
    exit /b 1
)
set "INSTALL_PS=%SUPPORT_DIR%install_external_files.ps1"
if not exist "%INSTALL_PS%" set "INSTALL_PS=%BUNDLE_DIR%install_external_files.ps1"
if not exist "%INSTALL_PS%" (
    call :setup_log "ERROR source\install_external_files.ps1 is required."
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS%" -BundleRoot "%BUNDLE_ARG%"
if errorlevel 1 (
    call :setup_log "ERROR staged external file installation failed."
    exit /b 1
)
call :setup_log "Staged external file installation complete."
exit /b 0

:phase_deploy_touchtools
call :setup_log "Phase: deploy TouchTools media"
set "DEPLOY_PS=%SUPPORT_DIR%deploy_touchtools_media.ps1"
if not exist "%DEPLOY_PS%" set "DEPLOY_PS=%BUNDLE_DIR%deploy_touchtools_media.ps1"
if not exist "%DEPLOY_PS%" (
    call :setup_log "ERROR source\deploy_touchtools_media.ps1 is required."
    exit /b 1
)
set "DEPLOY_EXTRA="
if "%NO_PAUSE%"=="1" set "DEPLOY_EXTRA=-NoPause"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_PS%" -BundleRoot "%BUNDLE_ARG%" %DEPLOY_EXTRA%
if errorlevel 1 (
    call :setup_log "ERROR TouchTools media deploy failed."
    exit /b 1
)
call :setup_log "TouchTools media deploy complete."
exit /b 0

:mirror_collect
if not exist "%~1\" (
    call :setup_log "SKIP missing source: %~1"
    exit /b 0
)
set "MIRROR_LABEL=%~3"
if "%MIRROR_LABEL%"=="" set "MIRROR_LABEL=%~1"
set "PROGRESS_PS=%SUPPORT_DIR%copy_tree_with_progress.ps1"
if not exist "%PROGRESS_PS%" set "PROGRESS_PS=%BUNDLE_DIR%copy_tree_with_progress.ps1"
if exist "%PROGRESS_PS%" (
    call :setup_log "Copying with progress bar: %MIRROR_LABEL%"
    call :touch_progress_heartbeat "copy:%MIRROR_LABEL%"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PROGRESS_PS%" -Action Copy -Source "%~1" -Destination "%~2" -Label "%MIRROR_LABEL%"
    if errorlevel 8 (
        call :setup_log "WARN snapshot copy failed: %~1"
        exit /b 0
    )
) else (
    call :setup_log "Copying with robocopy progress: %MIRROR_LABEL%"
    call :setup_log "Progress %% / ETA appear below. This is normal for large folders."
    call :touch_progress_heartbeat "robocopy:%MIRROR_LABEL%"
    robocopy "%~1" "%~2" /MIR /R:2 /W:1 /XJ /NFL /NDL /ETA
    if errorlevel 8 (
        call :setup_log "WARN snapshot copy failed: %~1"
        exit /b 0
    )
)
call :setup_log "Collected: %~1"
exit /b 0

:remove_tree_progress
if not exist "%~1\" exit /b 0
set "REMOVE_LABEL=%~2"
if "%REMOVE_LABEL%"=="" set "REMOVE_LABEL=Clearing %~1"
set "PROGRESS_PS=%SUPPORT_DIR%copy_tree_with_progress.ps1"
if not exist "%PROGRESS_PS%" set "PROGRESS_PS=%BUNDLE_DIR%copy_tree_with_progress.ps1"
if exist "%PROGRESS_PS%" (
    call :setup_log "Removing with progress bar: %REMOVE_LABEL%"
    call :touch_progress_heartbeat "remove:%REMOVE_LABEL%"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PROGRESS_PS%" -Action Remove -Path "%~1" -Label "%REMOVE_LABEL%"
) else (
    call :setup_log "Removing previous copy (no progress helper found): %~1"
    call :touch_progress_heartbeat "remove-rmdir:%REMOVE_LABEL%"
    rmdir /S /Q "%~1"
)
if exist "%~1\" (
    call :setup_log "WARN could not fully remove: %~1"
)
exit /b 0

:copy_file_collect
if not exist "%~1" (
    call :setup_log "SKIP missing source file: %~1"
    exit /b 0
)
call :touch_progress_heartbeat "copy-file:%~nx1"
copy /Y "%~1" "%~2" >nul
if errorlevel 1 (
    call :setup_log "WARN file copy failed: %~1"
    exit /b 0
)
call :setup_log "Collected: %~1"
exit /b 0

:mirror_install
if not exist "%~1\" (
    call :setup_log "SKIP snapshot section missing: %~1"
    exit /b 0
)
if not exist "%~2\" mkdir "%~2" >nul 2>&1
set "MIRROR_LABEL=%~3"
if "%MIRROR_LABEL%"=="" set "MIRROR_LABEL=%~2"
set "PROGRESS_PS=%SUPPORT_DIR%copy_tree_with_progress.ps1"
if not exist "%PROGRESS_PS%" set "PROGRESS_PS=%BUNDLE_DIR%copy_tree_with_progress.ps1"
if exist "%PROGRESS_PS%" (
    call :setup_log "Installing with progress bar: %MIRROR_LABEL%"
    call :touch_progress_heartbeat "install:%MIRROR_LABEL%"
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PROGRESS_PS%" -Action Copy -Source "%~1" -Destination "%~2" -Label "%MIRROR_LABEL%"
    if errorlevel 8 (
        call :setup_log "ERROR install copy failed: %~2"
        exit /b 1
    )
) else (
    call :setup_log "Installing with robocopy progress: %MIRROR_LABEL%"
    robocopy "%~1" "%~2" /E /R:2 /W:1 /XJ /NFL /NDL /ETA
    if errorlevel 8 (
        call :setup_log "ERROR install copy failed: %~2"
        exit /b 1
    )
)
call :setup_log "Installed: %~2"
exit /b 0

:set_log_profile
if /I "%~1"=="everything" (
    set "LOG_PROFILE=everything"
    set "LOG_PROFILE_LABEL=Everything"
    exit /b 0
)
if /I "%~1"=="all" (
    set "LOG_PROFILE=everything"
    set "LOG_PROFILE_LABEL=Everything"
    exit /b 0
)
if /I "%~1"=="script-errors" (
    set "LOG_PROFILE=script-errors"
    set "LOG_PROFILE_LABEL=In-Script errors"
    exit /b 0
)
if /I "%~1"=="in-script-errors" (
    set "LOG_PROFILE=script-errors"
    set "LOG_PROFILE_LABEL=In-Script errors"
    exit /b 0
)
if /I "%~1"=="program-crash" (
    set "LOG_PROFILE=program-crash"
    set "LOG_PROFILE_LABEL=Tecan Program Crash"
    exit /b 0
)
if /I "%~1"=="tecan-program-crash" (
    set "LOG_PROFILE=program-crash"
    set "LOG_PROFILE_LABEL=Tecan Program Crash"
    exit /b 0
)
if /I "%~1"=="import-errors" (
    set "LOG_PROFILE=import-errors"
    set "LOG_PROFILE_LABEL=Import errors"
    exit /b 0
)
echo ERROR: Unknown log profile "%~1"
exit /b 1

:touch_progress_heartbeat
if "%TECAN_SETUP_HEARTBEAT%"=="" exit /b 0
(
    echo updated=%DATE% %TIME%
    echo status=%~1
) > "%TECAN_SETUP_HEARTBEAT%"
exit /b 0

:start_stall_watchdog
set "STALL_PHASE=%~1"
set "STALL_SECONDS=%~2"
set "STALL_GRACE=%~3"
if "%STALL_SECONDS%"=="" set "STALL_SECONDS=120"
if "%STALL_GRACE%"=="" set "STALL_GRACE=45"
if not exist "%TEMP_DIR%" mkdir "%TEMP_DIR%" >nul 2>&1
set "TECAN_SETUP_HEARTBEAT=%TEMP_DIR%progress_heartbeat.txt"
set "TECAN_SETUP_STALL_ERROR=%TEMP_DIR%tecan_bundle_setup_STALL.error.txt"
set "TECAN_SETUP_STALL_COMPLETE=%TEMP_DIR%progress_complete.flag"
set "TECAN_SETUP_STALL_PID=%TEMP_DIR%stall_watchdog.pid"
if exist "%TECAN_SETUP_STALL_COMPLETE%" del /F /Q "%TECAN_SETUP_STALL_COMPLETE%" >nul 2>&1
if exist "%TECAN_SETUP_HEARTBEAT%" del /F /Q "%TECAN_SETUP_HEARTBEAT%" >nul 2>&1
if exist "%TECAN_SETUP_STALL_ERROR%" (
    move /Y "%TECAN_SETUP_STALL_ERROR%" "%TECAN_SETUP_STALL_ERROR%.prev.txt" >nul 2>&1
)
call :stop_stall_watchdog_process
set "WATCHDOG_PS=%SUPPORT_DIR%stall_watchdog.ps1"
if not exist "%WATCHDOG_PS%" set "WATCHDOG_PS=%BUNDLE_DIR%stall_watchdog.ps1"
if not exist "%WATCHDOG_PS%" (
    call :setup_log "WARN stall_watchdog.ps1 missing; freeze errors will not be written to temp_files."
    exit /b 0
)
call :setup_log "Stall watchdog on for %STALL_PHASE% (freeze error: %TECAN_SETUP_STALL_ERROR%)"
set "STALL_WATCHDOG_PID="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -WindowStyle Hidden -PassThru -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%WATCHDOG_PS%','-HeartbeatFile','%TECAN_SETUP_HEARTBEAT%','-ErrorFile','%TECAN_SETUP_STALL_ERROR%','-CompleteFile','%TECAN_SETUP_STALL_COMPLETE%','-PidFile','%TECAN_SETUP_STALL_PID%','-LogFile','%LOG%','-Phase','%STALL_PHASE%','-StallSeconds',%STALL_SECONDS%,'-StartupGraceSeconds',%STALL_GRACE%); $p.Id"`) do set "STALL_WATCHDOG_PID=%%I"
call :touch_progress_heartbeat "watchdog-started:%STALL_PHASE%"
exit /b 0

:stop_stall_watchdog
if not "%TECAN_SETUP_STALL_COMPLETE%"=="" (
    echo done> "%TECAN_SETUP_STALL_COMPLETE%"
)
call :stop_stall_watchdog_process
if exist "%TECAN_SETUP_STALL_ERROR%" (
    call :setup_log "ERROR freeze/stall was detected. See %TECAN_SETUP_STALL_ERROR%"
    set "SETUP_ERROR=1"
)
set "TECAN_SETUP_HEARTBEAT="
exit /b 0

:stop_stall_watchdog_process
if not "%STALL_WATCHDOG_PID%"=="" (
    taskkill /PID %STALL_WATCHDOG_PID% /F >nul 2>&1
    set "STALL_WATCHDOG_PID="
)
if not "%TECAN_SETUP_STALL_PID%"=="" if exist "%TECAN_SETUP_STALL_PID%" (
    for /f "usebackq delims=" %%P in ("%TECAN_SETUP_STALL_PID%") do taskkill /PID %%P /F >nul 2>&1
    del /F /Q "%TECAN_SETUP_STALL_PID%" >nul 2>&1
)
exit /b 0

:setup_log
echo %~1
>>"%LOG%" echo [%DATE% %TIME%] %~1
exit /b 0

:finish
echo.
call :stop_stall_watchdog_process
if exist "%TEMP_DIR%tecan_bundle_setup_STALL.error.txt" (
    call :setup_log "ERROR freeze/stall report present: %TEMP_DIR%tecan_bundle_setup_STALL.error.txt"
    set "SETUP_ERROR=1"
)
if "%SETUP_ERROR%"=="1" (
    call :setup_log "Support utility finished with errors. Review %LOG%."
    call :offer_open_temp_files
    if not "%NO_PAUSE%"=="1" pause
    exit /b 1
)
call :setup_log "Support utility completed successfully."
call :setup_log "Results folder: %TEMP_DIR%"
call :offer_open_temp_files
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:offer_open_temp_files
if "%NO_PAUSE%"=="1" exit /b 0
if not exist "%TEMP_DIR%" exit /b 0
echo.
choice /C YN /N /M "Open the temp_files results folder now? [Y/N]: "
if errorlevel 2 exit /b 0
explorer "%TEMP_DIR%"
exit /b 0

:usage
echo Usage:
echo   run_tecan_bundle_setup.bat --logs-only [--no-pause]
echo   run_tecan_bundle_setup.bat --logs-only --log-profile ^<everything^|script-errors^|program-crash^|import-errors^> [--no-pause]
echo   run_tecan_bundle_setup.bat --logs-menu [--no-pause]
echo   run_tecan_bundle_setup.bat --collect-instrument [--no-pause]
echo   run_tecan_bundle_setup.bat --collect-method-source [--no-pause]
echo   run_tecan_bundle_setup.bat --install-instrument [--no-pause]
echo   run_tecan_bundle_setup.bat --install-external-files [--no-pause]
echo   run_tecan_bundle_setup.bat --deploy-touchtools [--no-pause]
exit /b 0

:usage_error
call :usage
exit /b 2

:load_settings
if exist "%SETTINGS_FILE%" call "%SETTINGS_FILE%"
call :normalize_settings
exit /b 0

:normalize_settings
call :validate_number "%LOG_LOOKBACK_DAYS%" 1 90
if errorlevel 1 set "LOG_LOOKBACK_DAYS=1"
call :validate_number "%LIKELY_CAUSE_MAX_RECORDS%" 20 5000
if errorlevel 1 set "LIKELY_CAUSE_MAX_RECORDS=200"
call :validate_number "%WINDOWS_EVENT_MAX_EVENTS%" 100 10000
if errorlevel 1 set "WINDOWS_EVENT_MAX_EVENTS=2000"
exit /b 0

:prompt_numeric_setting
set "SETTING_NAME=%~1"
set "SETTING_LABEL=%~2"
set "SETTING_MIN=%~3"
set "SETTING_MAX=%~4"
set "SETTING_CURRENT=!%SETTING_NAME%!"
echo.
echo Current %SETTING_LABEL%: !SETTING_CURRENT!
set "NEW_VALUE="
set /P "NEW_VALUE=Enter %SETTING_LABEL% [%SETTING_MIN%-%SETTING_MAX%], blank to cancel: "
if not defined NEW_VALUE exit /b 0
call :validate_number "%NEW_VALUE%" "%SETTING_MIN%" "%SETTING_MAX%"
if errorlevel 1 (
    echo Invalid value. Enter a number from %SETTING_MIN% to %SETTING_MAX%.
    exit /b 1
)
set "%SETTING_NAME%=%NEW_VALUE%"
call :save_settings
echo Saved %SETTING_LABEL%: %NEW_VALUE%
exit /b 0

:reset_settings
set "LOG_LOOKBACK_DAYS=1"
set "LIKELY_CAUSE_MAX_RECORDS=200"
set "WINDOWS_EVENT_MAX_EVENTS=2000"
call :save_settings
echo Settings reset to defaults.
exit /b 0

:save_settings
(
    echo @echo off
    echo rem Settings saved by run_tecan_bundle_setup.bat for this copied bundle.
    echo set "LOG_LOOKBACK_DAYS=%LOG_LOOKBACK_DAYS%"
    echo set "LIKELY_CAUSE_MAX_RECORDS=%LIKELY_CAUSE_MAX_RECORDS%"
    echo set "WINDOWS_EVENT_MAX_EVENTS=%WINDOWS_EVENT_MAX_EVENTS%"
) > "%SETTINGS_FILE%"
exit /b 0

:validate_number
set "VALUE_TO_CHECK=%~1"
set "MIN_VALUE=%~2"
set "MAX_VALUE=%~3"
echo(%VALUE_TO_CHECK%| findstr /R "^[0-9][0-9]*$" >nul || exit /b 1
set /A VALUE_NUM=%VALUE_TO_CHECK% >nul 2>&1
if errorlevel 1 exit /b 1
if %VALUE_NUM% LSS %MIN_VALUE% exit /b 1
if %VALUE_NUM% GTR %MAX_VALUE% exit /b 1
exit /b 0

:require_admin
call net session >nul 2>&1
if not errorlevel 1 exit /b 0
call :setup_log "Administrator privileges are required for this step."
if "%NO_PAUSE%"=="1" exit /b 1
echo.
choice /C YN /N /M "Relaunch this utility as Administrator now? [Y/N]: "
if errorlevel 2 exit /b 1
if "%ELEVATE_ARGS%"=="" set "ELEVATE_ARGS=--install-instrument --no-pause"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$args = @('%ELEVATE_ARGS%'.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)); Start-Process -FilePath '%~f0' -WorkingDirectory '%BUNDLE_ARG%' -Verb RunAs -ArgumentList $args | Out-Null"
exit /b 1
