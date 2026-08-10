param(
    [string]$Profile = $env:SETUP_LOG_PROFILE,
    [string]$OutputRoot = $env:SETUP_LOG_OUTPUT,
    [string]$BundleRoot = $env:BUNDLE_ARG,
    [int]$SinceDays = 0,
    [int]$LikelyCauseMaxRecords = 200,
    [int]$EventLogMaxEvents = 2000,
    [switch]$CaptureFluentControlInfopad
)

$ErrorActionPreference = 'Stop'

function Normalize-LogProfile {
    param([string]$Value)
    $text = ([string]$Value).Trim().ToLowerInvariant()
    switch ($text) {
        { $_ -in @('', 'all', 'everything', '1') } { return 'everything' }
        { $_ -in @('script', 'script-errors', 'in-script', 'in-script-errors', '2') } { return 'script-errors' }
        { $_ -in @('crash', 'program-crash', 'tecan-program-crash', '3') } { return 'program-crash' }
        { $_ -in @('import', 'import-errors', '4') } { return 'import-errors' }
        default { throw "Unknown log profile: $Value" }
    }
}

function New-VersionedErrorLogOutputPath {
    param(
        [string]$OutputRoot,
        [datetime]$Now = (Get-Date)
    )
    $dateLabel = $Now.ToString('MM-dd-yyyy', [System.Globalization.CultureInfo]::InvariantCulture)
    $baseName = "error_logs_$dateLabel"
    $nextVersion = 1
    if (Test-Path -LiteralPath $OutputRoot) {
        $escapedBaseName = [regex]::Escape($baseName)
        $existingVersions = @(
            Get-ChildItem -LiteralPath $OutputRoot -Directory -ErrorAction SilentlyContinue |
                ForEach-Object {
                    if ($_.Name -match "^${escapedBaseName}_v(?<version>\d+)$") {
                        [int]$Matches['version']
                    }
                }
        )
        if ($existingVersions.Count -gt 0) {
            $nextVersion = (($existingVersions | Measure-Object -Maximum).Maximum + 1)
        }
    }
    for ($version = $nextVersion; $version -le 9999; $version++) {
        $name = "{0}_v{1}" -f $baseName, $version
        $candidate = Join-Path $OutputRoot $name
        if (-not (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw "Could not allocate a versioned error log output folder under $OutputRoot"
}

function New-RootSpec {
    param(
        [string]$Label,
        [string[]]$Paths,
        [string[]]$Patterns
    )
    @{
        Label = $Label
        Paths = $Paths
        Patterns = $Patterns
    }
}

function Write-CollectionProgress {
    param(
        [string]$Label,
        [int]$Current,
        [int]$Total,
        [string]$Item = "",
        [switch]$Final
    )
    $pct = 0
    if ($Total -gt 0) {
        $pct = [int][Math]::Min(100, [Math]::Floor(100.0 * $Current / $Total))
    } elseif ($Final) {
        $pct = 100
    }
    $width = 32
    $filled = [int][Math]::Floor($width * $pct / 100.0)
    if ($filled -gt $width) { $filled = $width }
    $bar = ("#" * $filled) + ("-" * ($width - $filled))
    $itemText = ""
    if (-not [string]::IsNullOrWhiteSpace($Item)) {
        $itemText = $Item
        if ($itemText.Length -gt 42) {
            $itemText = "..." + $itemText.Substring($itemText.Length - 39)
        }
        $itemText = "  " + $itemText
    }
    $prefix = if ([string]::IsNullOrWhiteSpace($Label)) { "" } else { "$Label " }
    $line = "{0}[{1}] {2,3}%  {3:N0}/{4:N0}{5}" -f $prefix, $bar, $pct, $Current, $Total, $itemText
    if ($Final) {
        Write-Host ($line.PadRight(110))
    } else {
        Write-Host ("`r" + $line.PadRight(110)) -NoNewline
    }
}

function Copy-DiagnosticFile {
    param(
        [string]$Source,
        [string]$Destination
    )
    $parent = [IO.Path]::GetDirectoryName($Destination)
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    try {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force -ErrorAction Stop
        return 'copied'
    } catch {
        try {
            $inStream = [System.IO.File]::Open(
                $Source,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            try {
                $outStream = [System.IO.File]::Open(
                    $Destination,
                    [System.IO.FileMode]::Create,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
                try {
                    $inStream.CopyTo($outStream)
                    $outStream.Flush($true)
                } finally {
                    $outStream.Dispose()
                }
            } finally {
                $inStream.Dispose()
            }
            return 'locked_copy'
        } catch {
            return "failed: $($_.Exception.Message)"
        }
    }
}

function Test-AnyPattern {
    param(
        [string]$Name,
        [string[]]$Patterns
    )
    foreach ($pattern in $Patterns) {
        if ($Name -like $pattern) {
            return $true
        }
    }
    return $false
}

function Copy-RecentRoot {
    param(
        [hashtable]$RootSpec,
        [string]$DestinationRoot,
        [int]$SinceDays
    )
    $result = [ordered]@{
        label = $RootSpec.Label
        sources = @()
        copied = 0
        locked_copy = 0
        skipped = 0
        failed = @()
    }
    $cutoff = (Get-Date).AddDays(-1 * $SinceDays)
    Write-Host ""
    Write-Host ("==== Log root: {0} ====" -f $RootSpec.Label)
    Write-Host "Scanning for recent matching files..."
    foreach ($sourceRoot in $RootSpec.Paths) {
        $sourceItem = [ordered]@{ path = $sourceRoot; exists = $false; matched = 0 }
        if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
            Write-Host ("SKIP missing source: {0}" -f $sourceRoot)
            $result.skipped++
            $result.sources += $sourceItem
            continue
        }
        $sourceItem.exists = $true
        Write-Host ("Scanning: {0}" -f $sourceRoot)
        $files = @(
            Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -ge $cutoff -and (Test-AnyPattern -Name $_.Name -Patterns $RootSpec.Patterns) } |
                Sort-Object -Property FullName -Unique
        )
        $total = $files.Count
        Write-Host ("Matched {0:N0} file(s) under {1}" -f $total, $sourceRoot)
        $index = 0
        $lastBeat = Get-Date
        foreach ($file in $files) {
            $sourceItem.matched++
            $index++
            $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart(
                [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
            )
            $target = Join-Path (Join-Path $DestinationRoot $RootSpec.Label) $relative
            $status = Copy-DiagnosticFile -Source $file.FullName -Destination $target
            if ($status -eq 'copied') {
                $result.copied++
            } elseif ($status -eq 'locked_copy') {
                $result.locked_copy++
            } else {
                $result.failed += "$relative ($status)"
            }
            if ($index -eq $total -or (((Get-Date) - $lastBeat).TotalMilliseconds -ge 150)) {
                Write-CollectionProgress -Label $RootSpec.Label -Current $index -Total $total -Item $relative
                $lastBeat = Get-Date
            }
        }
        if ($total -gt 0) {
            Write-CollectionProgress -Label $RootSpec.Label -Current $total -Total $total -Item "done" -Final
        }
        $result.sources += $sourceItem
    }
    return $result
}

function Copy-BundleEvidence {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [string]$ProfileName
    )
    $result = [ordered]@{ source = $SourceRoot; copied = 0; failed = @() }
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        return $result
    }
    $singleFiles = @(
        'RECREATE_SCRIPT.md',
        'README.md',
        'delivery_manifest.json',
        'GENERATION_WORKFLOW.md',
        'generation_manifest.json',
        'metadata.json',
        'request.spec.yaml',
        'protocol.ir.json',
        'generated\protocol.py',
        'source\delivery_manifest.json',
        'source\generation_manifest.json',
        'source\metadata.json',
        'source\GENERATION_WORKFLOW.md',
        'source\request.spec.yaml',
        'source\generated\protocol.py',
        'source\DRIVER_SAFE_REPAIR.md',
        'source\driver_safe_minimal_edit_diff.md',
        'source\protocol.ir.json',
        'source\protocol_draft.py',
        'source\touchtools_deploy.json'
    )
    if ($ProfileName -in @('everything', 'import-errors')) {
        $singleFiles += @(
            'source\reports\project_import_report.json',
            'source\reports\project_import_report.md',
            'source\reports\validation_report.json',
            'source\reports\validation_report.md',
            'source\reports\compile_report.md',
            'source\reports\simulation_report.md',
            'reports\project_import_report.json',
            'reports\project_import_report.md',
            'reports\validation_report.json',
            'reports\validation_report.md',
            'reports\compile_report.md',
            'reports\simulation_report.md',
            'reports\worktable_changes.md',
            'reports\worktable.patch.json'
        )
    }
    foreach ($relative in $singleFiles | Sort-Object -Unique) {
        $source = Join-Path $SourceRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            continue
        }
        $target = Join-Path (Join-Path $DestinationRoot 'bundle_context') $relative
        $status = Copy-DiagnosticFile -Source $source -Destination $target
        if ($status -in @('copied', 'locked_copy')) {
            $result.copied++
        } else {
            $result.failed += "$relative ($status)"
        }
    }
    return $result
}

function Write-DumpListing {
    param(
        [string]$DestinationRoot,
        [int]$SinceDays
    )
    $dumpRoot = Join-Path $env:ProgramData 'Tecan\VisionX\DumpFiles'
    $dumpOutput = Join-Path $DestinationRoot 'crash_dumps'
    New-Item -ItemType Directory -Force -Path $dumpOutput | Out-Null
    $listing = Join-Path $dumpOutput 'visionx_dumpfiles_listing.txt'
    if (-not (Test-Path -LiteralPath $dumpRoot -PathType Container)) {
        "DumpFiles folder missing: $dumpRoot" | Out-File -LiteralPath $listing -Encoding utf8
        return @{ source = $dumpRoot; count = 0; copied = 0; note = 'missing' }
    }
    $cutoff = (Get-Date).AddDays(-1 * $SinceDays)
    $files = Get-ChildItem -LiteralPath $dumpRoot -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $cutoff } |
        Sort-Object LastWriteTime -Descending
    $files |
        Select-Object FullName, Length, LastWriteTime |
        Format-Table -AutoSize |
        Out-File -LiteralPath $listing -Encoding utf8
    return @{ source = $dumpRoot; count = @($files).Count; copied = 0; note = 'listing_only_large_dumps_not_copied' }
}

function Write-ScriptEditorDumpScan {
    param(
        [string]$DestinationRoot,
        [int]$SinceDays,
        [string]$BundleRoot
    )
    $dumpRoot = Join-Path $env:ProgramData 'Tecan\VisionX\DumpFiles'
    $scanOutput = Join-Path $DestinationRoot 'script_editor_dump_scan'
    $jsonPath = Join-Path $scanOutput 'script_editor_dump_scan.json'
    New-Item -ItemType Directory -Force -Path $scanOutput | Out-Null
    $cliRoot = Resolve-ProtocolBuilderRoot -BundleRoot $BundleRoot
    if (-not $cliRoot) {
        [ordered]@{ status = 'cli_missing'; dump_root = $dumpRoot; json = $jsonPath; finding_count = 0 } |
            ConvertTo-Json | Out-File -LiteralPath $jsonPath -Encoding utf8
        return @{ status = 'cli_missing'; dump_root = $dumpRoot; json = $jsonPath; finding_count = 0 }
    }
    $python = Resolve-PythonExe -CliRoot $cliRoot
    Push-Location $cliRoot
    try {
        & $python -m fluent_pipeline.dump_error_scan $dumpRoot --since-days $SinceDays --json-out $jsonPath 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $jsonPath -PathType Leaf)) {
        [ordered]@{ status = 'scan_failed'; dump_root = $dumpRoot; json = $jsonPath; finding_count = 0 } |
            ConvertTo-Json | Out-File -LiteralPath $jsonPath -Encoding utf8
        return @{ status = 'scan_failed'; dump_root = $dumpRoot; json = $jsonPath; finding_count = 0 }
    }
    $report = Read-DiagnosisJson -Path $jsonPath
    return @{
        status = 'complete'
        dump_root = $dumpRoot
        json = $jsonPath
        finding_count = @($report.findings).Count
        scanned_file_count = @($report.scanned_files).Count
    }
}

function Write-FluentControlInfopadEvidence {
    param([string]$DestinationRoot)

    $infopadOutput = Join-Path $DestinationRoot 'fluentcontrol_infopad'
    $jsonPath = Join-Path $infopadOutput 'fluentcontrol_infopad.json'
    New-Item -ItemType Directory -Force -Path $infopadOutput | Out-Null
    $capture = [ordered]@{
        schema_version = 'tecan.fluentcontrol_infopad.v1'
        captured_at = (Get-Date).ToString('o')
        status = 'unknown'
        process_ids = @()
        application_window_count = 0
        messages = @()
    }

    try {
        Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
        $systemSwProcessIds = @(Get-Process -Name 'SystemSW' -ErrorAction SilentlyContinue | ForEach-Object Id)
        $capture.process_ids = @($systemSwProcessIds)
        if ($systemSwProcessIds.Count -eq 0) {
            $capture.status = 'not_running'
        } else {
            $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition
            )
            $messages = New-Object 'System.Collections.Generic.List[object]'
            foreach ($window in $windows) {
                if ($systemSwProcessIds -notcontains $window.Current.ProcessId -or
                    $window.Current.AutomationId -ne 'ApplicationMainWindow') {
                    continue
                }
                $capture.application_window_count++
                foreach ($element in $window.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition
                )) {
                    try {
                        # FluentControl 3.7 exposes embedded Infopad rows as
                        # TextBlock elements with AutomationId msgText. They
                        # are not top-level dialogs and therefore never appear
                        # in a window-only collector.
                        if ($element.Current.AutomationId -ne 'msgText') {
                            continue
                        }
                        $message = $element.Current.Name
                        if ([string]::IsNullOrWhiteSpace($message)) {
                            continue
                        }
                        $commandNumber = ''
                        if ($message -match '^(?<command>\d+):\s*') {
                            $commandNumber = $Matches['command']
                        }
                        $messages.Add([ordered]@{
                            process_id = $window.Current.ProcessId
                            command_number = $commandNumber
                            control_type = $element.Current.ControlType.ProgrammaticName
                            automation_id = $element.Current.AutomationId
                            class_name = $element.Current.ClassName
                            message = $message
                        }) | Out-Null
                    } catch {
                        # A UI element can disappear while FluentControl redraws.
                    }
                }
            }
            $capture.messages = @($messages.ToArray())
            $capture.status = if ($messages.Count -gt 0) { 'captured' } else { 'no_messages' }
        }
    } catch {
        $capture.status = 'automation_unavailable'
        $capture.error = $_.Exception.Message
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($jsonPath, ($capture | ConvertTo-Json -Depth 8), $utf8NoBom)
    return @{
        status = $capture.status
        json = $jsonPath
        process_count = @($capture.process_ids).Count
        application_window_count = $capture.application_window_count
        message_count = @($capture.messages).Count
    }
}

function Write-EventLogEvidence {
    param(
        [string]$DestinationRoot,
        [int]$SinceDays,
        [int]$MaxEvents = 2000
    )
    $eventOutput = Join-Path $DestinationRoot 'windows_event_logs'
    New-Item -ItemType Directory -Force -Path $eventOutput | Out-Null
    $path = Join-Path $eventOutput 'windows_application_tecan_errors.txt'
    try {
        $start = (Get-Date).AddDays(-1 * $SinceDays)
        $events = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $start } -MaxEvents $MaxEvents -ErrorAction Stop |
            Where-Object {
                $_.ProviderName -match 'Tecan|VisionX|Fluent|Application Error|Windows Error Reporting|.NET Runtime' -or
                $_.Message -match 'Tecan|VisionX|FluentControl|SystemSW'
            } |
            Sort-Object TimeCreated -Descending |
            Select-Object -First $MaxEvents TimeCreated, ProviderName, Id, LevelDisplayName, Message
        $events | Format-List | Out-File -LiteralPath $path -Encoding utf8
        return @{ copied = @($events).Count; path = $path; note = 'application_event_log' }
    } catch {
        "Could not read Windows Application event log: $($_.Exception.Message)" | Out-File -LiteralPath $path -Encoding utf8
        return @{ copied = 0; path = $path; note = 'event_log_unavailable' }
    }
}

function Get-ImportScanFileKind {
    param([System.IO.FileInfo]$File)
    $full = $File.FullName.ToLowerInvariant()
    $name = $File.Name.ToLowerInvariant()
    if ($File.Extension -ieq '.ulf') {
        return 'ulf'
    }
    if ($full -match '\\visionx_audit_(trail|archive)\\' -or $name -match 'audit') {
        return 'audit'
    }
    if ($full -match '\\loggingserver_logfiles\\|\\visionx_logs\\|\\visionx_logfiles\\|\\fluentcontrol_programdata_logs\\') {
        return 'program_log'
    }
    if ($full -match '\\datastore_iot_client_logs\\') {
        return 'datastore_iot_log'
    }
    return 'supporting_log'
}

function Write-ImportErrorScan {
    param([string]$DestinationRoot)
    $files = Get-ChildItem -LiteralPath $DestinationRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in @('.ulf', '.log', '.txt', '.csv') -and
            $_.FullName -notmatch '\\likely_causes\\'
        }
    $scanFiles = @($files | ForEach-Object {
        [ordered]@{
            path = $_.FullName
            source_kind = Get-ImportScanFileKind -File $_
        }
    })
    $rules = @(
        @{
            id = 'checksum'
            title = 'Checksum or unauthorized XML modification'
            pattern = 'InvalidChecksumException|ChecksumException|XML checksum error|unauthorized modification|checksum.{0,160}(invalid|error|recalculat|unauthorized|required|failed)|import-clean.{0,80}(checksum|recompute)'
        },
        @{
            id = 'missing_subroutine'
            title = 'Missing or unloaded subroutine'
            pattern = '(unable|could not|cannot|failed).{0,120}(load|open|resolve|find).{0,100}(selected )?subroutine|subroutine.{0,160}(missing|not found|could not be found|cannot be found|ambiguous|unresolved)|missing.{0,80}subroutine|subroutine_reference_missing'
        },
        @{
            id = 'missing_file'
            title = 'Missing referenced file or payload'
            pattern = 'FileNotFoundException|DirectoryNotFoundException|Could not find file|Could not find a part of the path|Cannot find path|cannot open|could not open|failed to open|(missing|not found|unresolved).{0,180}(file|path|reference|dependency|worklist|\.gwl|\.csv|\.png|\.gif|\.jpg|\.vb|\.exe)'
        },
        @{
            id = 'zeia_import_failure'
            title = 'ZEIA import/load failure'
            pattern = '(ZEIA|ExportImportArchive|ImportArchive|Import|imported from).{0,200}(failed|error|exception|cannot|could not|invalid|aborted|rejected)|VX_APPFR_016_005|Failed to import'
        }
    )
    $findings = @()
    foreach ($rule in $rules) {
        $matches = @()
        foreach ($scanFile in $scanFiles) {
            try {
                $hits = Select-String -LiteralPath $scanFile.path -Pattern $rule.pattern -CaseSensitive:$false -ErrorAction Stop |
                    Select-Object -First 12
                foreach ($hit in $hits) {
                    $matches += [ordered]@{
                        file = $scanFile.path
                        source_kind = $scanFile.source_kind
                        line = $hit.LineNumber
                        text = (($hit.Line -replace '\s+', ' ').Trim())
                    }
                }
            } catch {
                $matches += [ordered]@{
                    file = $scanFile.path
                    source_kind = $scanFile.source_kind
                    line = 0
                    text = "scan failed: $($_.Exception.Message)"
                }
            }
        }
        $findings += [ordered]@{
            id = $rule.id
            title = $rule.title
            count = @($matches).Count
            source_kinds = @($matches | Group-Object { $_.source_kind } | Sort-Object Name | ForEach-Object {
                [ordered]@{ kind = $_.Name; count = $_.Count }
            })
            matches = @($matches)
        }
    }
    $scanOutput = Join-Path $DestinationRoot 'import_scan'
    New-Item -ItemType Directory -Force -Path $scanOutput | Out-Null
    $jsonPath = Join-Path $scanOutput 'import_error_scan.json'
    $mdPath = Join-Path $scanOutput 'import_error_scan.md'
    [ordered]@{
        schema_version = 'tecan.import_error_scan.v1'
        generated_at = (Get-Date).ToString('o')
        scanned_file_count = @($scanFiles).Count
        scanned_files = @($scanFiles)
        scanned_source_kinds = @($scanFiles | Group-Object { $_.source_kind } | Sort-Object Name | ForEach-Object {
            [ordered]@{ kind = $_.Name; count = $_.Count }
        })
        findings = $findings
    } | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $jsonPath -Encoding utf8
    $lines = @('# Import Error Scan', '')
    $lines += "Scanned files: $(@($scanFiles).Count)"
    foreach ($group in @($scanFiles | Group-Object { $_.source_kind } | Sort-Object Name)) {
        $lines += "- $($group.Name): $($group.Count)"
    }
    $lines += ""
    foreach ($finding in $findings) {
        $lines += "## $($finding.title)"
        $lines += ""
        if ($finding.count -eq 0) {
            $lines += "- No matching evidence found."
            $lines += ""
            continue
        }
        foreach ($match in $finding.matches | Select-Object -First 10) {
            $lines += ("- [{0}] ``{1}:{2}`` {3}" -f $match['source_kind'], $match['file'], $match['line'], $match['text'])
        }
        $lines += ""
    }
    $lines | Out-File -LiteralPath $mdPath -Encoding utf8
    return @{ markdown = $mdPath; json = $jsonPath; finding_count = @($findings | Where-Object { $_.count -gt 0 }).Count }
}

function Resolve-ProtocolBuilderRoot {
    param([string]$BundleRoot)
    $candidates = @(
        (Join-Path $BundleRoot '..\..\source\03-protocol-builder'),
        (Join-Path $PSScriptRoot '..\..\..\source\03-protocol-builder'),
        (Join-Path $PSScriptRoot '..\..\source\03-protocol-builder'),
        (Join-Path $PSScriptRoot '..'),
        $PSScriptRoot
    )
    foreach ($candidate in $candidates) {
        $resolved = [IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath (Join-Path $resolved 'fluent_pipeline\cli\__main__.py') -PathType Leaf) {
            return $resolved
        }
    }
    return ''
}

function Resolve-PythonExe {
    param([string]$CliRoot)
    if ($CliRoot) {
        $venv = [IO.Path]::GetFullPath((Join-Path $CliRoot '..\..\.venv\Scripts\python.exe'))
        if (Test-Path -LiteralPath $venv -PathType Leaf) {
            return $venv
        }
    }
    return 'python'
}

function Get-ObjectProperty {
    param(
        [object]$Object,
        [string]$Name
    )
    if ($null -eq $Object) {
        return $null
    }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) {
        return $property.Value
    }
    return $null
}

function ConvertTo-DiagnosisText {
    param([object]$Value)
    if ($null -eq $Value) {
        return ''
    }
    if ($Value -is [string]) {
        return $Value.Trim()
    }
    if ($Value -is [System.ValueType]) {
        return ([string]$Value).Trim()
    }
    try {
        return (($Value | ConvertTo-Json -Depth 8 -Compress) -replace '\s+', ' ').Trim()
    } catch {
        return ([string]$Value).Trim()
    }
}

function Get-ObjectText {
    param(
        [object]$Object,
        [string]$Name
    )
    return ConvertTo-DiagnosisText (Get-ObjectProperty -Object $Object -Name $Name)
}

function Limit-DiagnosisText {
    param(
        [string]$Text,
        [int]$MaxLength = 900
    )
    $clean = ([string]$Text).Trim()
    if ($clean.Length -le $MaxLength) {
        return $clean
    }
    return ($clean.Substring(0, [Math]::Max(0, $MaxLength - 3)) + '...')
}

function ConvertTo-RawErrorRecord {
    param([object]$Value)
    $record = [ordered]@{}
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string]) {
        $text = Limit-DiagnosisText -Text $Value
        if ($text) {
            $record.text = $text
        }
        return $record
    }

    foreach ($pair in @(
        @('timestamp', 'timestamp'),
        @('source', 'source'),
        @('file', 'file'),
        @('line', 'line_number'),
        @('line', 'line'),
        @('level', 'level'),
        @('error_id', 'error_id'),
        @('script', 'script'),
        @('main_script', 'main_script'),
        @('script_source', 'script_source'),
        @('script_context', 'script_context'),
        @('script_line', 'script_line'),
        @('thread_id', 'thread_id'),
        @('command_hint', 'command_hint'),
        @('object', 'object'),
        @('message', 'message'),
        @('text', 'text'),
        @('raw_context', 'raw_context')
    )) {
        $target = $pair[0]
        $source = $pair[1]
        if ($record.Contains($target)) {
            continue
        }
        $valueText = Get-ObjectText -Object $Value -Name $source
        if ($valueText) {
            if ($target -in @('message', 'text', 'raw_context')) {
                $valueText = Limit-DiagnosisText -Text $valueText
            }
            $record[$target] = $valueText
        }
    }
    $detailLines = Get-ObjectProperty -Object $Value -Name 'detail_lines'
    if ($detailLines) {
        $details = @()
        foreach ($line in @($detailLines)) {
            $text = Limit-DiagnosisText -Text (ConvertTo-DiagnosisText $line) -MaxLength 300
            if ($text) {
                $details += $text
            }
        }
        if ($details.Count -gt 0) {
            $record.detail_lines = @($details)
        }
    }
    if ($record.Count -eq 0) {
        $record.text = Limit-DiagnosisText -Text (ConvertTo-DiagnosisText $Value)
    }
    return $record
}

function Add-DiagnosisRawError {
    param(
        [System.Collections.Generic.List[object]]$RawErrors,
        [hashtable]$Seen,
        [object]$Value
    )
    if ($null -eq $Value) {
        return
    }
    if (($Value -is [System.Array]) -or (
            $Value -is [System.Collections.IEnumerable] -and
            -not ($Value -is [string]) -and
            -not ($Value -is [System.Collections.IDictionary]) -and
            $Value.GetType().Name -ne 'PSCustomObject'
        )) {
        foreach ($entry in $Value) {
            Add-DiagnosisRawError -RawErrors $RawErrors -Seen $Seen -Value $entry
        }
        return
    }
    $record = ConvertTo-RawErrorRecord -Value $Value
    if ($null -eq $record -or $record.Count -eq 0) {
        return
    }
    $key = $record | ConvertTo-Json -Depth 8 -Compress
    if (-not $Seen.ContainsKey($key)) {
        $Seen[$key] = $true
        $RawErrors.Add($record) | Out-Null
    }
}

function New-DiagnosisItemFromObject {
    param(
        [object]$Source,
        [string]$SourceType,
        [int]$Index
    )
    if ($null -eq $Source) {
        return $null
    }
    $rawErrors = New-Object 'System.Collections.Generic.List[object]'
    $seen = @{}
    foreach ($name in @('records', 'raw_errors', 'rawErrors', 'matches', 'errors', 'evidence')) {
        Add-DiagnosisRawError -RawErrors $rawErrors -Seen $seen -Value (Get-ObjectProperty -Object $Source -Name $name)
    }
    $details = Get-ObjectProperty -Object $Source -Name 'details'
    if ($details) {
        foreach ($name in @('records', 'raw_errors', 'rawErrors', 'matches', 'errors', 'evidence')) {
            Add-DiagnosisRawError -RawErrors $rawErrors -Seen $seen -Value (Get-ObjectProperty -Object $details -Name $name)
        }
    }
    if ($rawErrors.Count -eq 0) {
        foreach ($name in @('detail', 'description', 'message')) {
            $text = Get-ObjectText -Object $Source -Name $name
            if ($text) {
                Add-DiagnosisRawError -RawErrors $rawErrors -Seen $seen -Value $text
            }
        }
    }

    $id = Get-ObjectText -Object $Source -Name 'id'
    if (-not $id) {
        $id = "{0}.{1}" -f $SourceType, $Index
    }
    $title = Get-ObjectText -Object $Source -Name 'title'
    if (-not $title) {
        $title = 'Diagnostic finding'
    }
    $likelyCause = Get-ObjectText -Object $Source -Name 'likely_workflow_defect'
    if (-not $likelyCause -and $details) {
        $likelyCause = Get-ObjectText -Object $details -Name 'likely_workflow_defect'
    }
    $suggestedFix = Get-ObjectText -Object $Source -Name 'suggested_fix'
    $nextSteps = @()
    foreach ($step in @((Get-ObjectProperty -Object $Source -Name 'next_steps'))) {
        $text = ConvertTo-DiagnosisText $step
        if ($text) {
            $nextSteps += $text
        }
    }
    $errorIds = @()
    foreach ($errorId in @((Get-ObjectProperty -Object $Source -Name 'error_ids'))) {
        $text = ConvertTo-DiagnosisText $errorId
        if ($text) {
            $errorIds += $text
        }
    }

    $item = [ordered]@{
        id = $id
        source_type = $SourceType
        severity = Get-ObjectText -Object $Source -Name 'severity'
        category = Get-ObjectText -Object $Source -Name 'category'
        title = $title
        likely_cause = $likelyCause
        suggested_fix = $suggestedFix
        raw_errors = @($rawErrors.ToArray())
    }
    if ($nextSteps.Count -gt 0) {
        $item.next_steps = @($nextSteps)
    }
    if ($errorIds.Count -gt 0) {
        $item.error_ids = @($errorIds)
    }
    return $item
}

function Add-DiagnosisItemsFromList {
    param(
        [System.Collections.Generic.List[object]]$Items,
        [hashtable]$Seen,
        [object]$Values,
        [string]$SourceType
    )
    $index = 0
    foreach ($value in @($Values)) {
        $index++
        $item = New-DiagnosisItemFromObject -Source $value -SourceType $SourceType -Index $index
        if ($null -eq $item) {
            continue
        }
        $key = "{0}|{1}|{2}" -f $item.source_type, $item.id, $item.title
        if (-not $Seen.ContainsKey($key)) {
            $Seen[$key] = $true
            $Items.Add($item) | Out-Null
        }
    }
}

function Get-DiagnosisItemsFromReport {
    param([object]$Report)
    $items = New-Object 'System.Collections.Generic.List[object]'
    $seen = @{}
    if ($null -eq $Report) {
        return @()
    }
    $logReport = Get-ObjectProperty -Object $Report -Name 'log_report'
    if ($logReport) {
        Add-DiagnosisItemsFromList -Items $items -Seen $seen -Values (Get-ObjectProperty -Object $logReport -Name 'diagnostics') -SourceType 'log_diagnostic'
    }
    Add-DiagnosisItemsFromList -Items $items -Seen $seen -Values (Get-ObjectProperty -Object $Report -Name 'diagnostics') -SourceType 'log_diagnostic'

    $diagnosis = Get-ObjectProperty -Object $Report -Name 'diagnosis'
    if ($diagnosis) {
        Add-DiagnosisItemsFromList -Items $items -Seen $seen -Values (Get-ObjectProperty -Object $diagnosis -Name 'findings') -SourceType 'static_diagnosis'
    }
    Add-DiagnosisItemsFromList -Items $items -Seen $seen -Values (Get-ObjectProperty -Object $Report -Name 'findings') -SourceType 'static_diagnosis'

    $summary = Get-ObjectProperty -Object $Report -Name 'summary'
    foreach ($errorText in @((Get-ObjectProperty -Object $summary -Name 'errors'))) {
        $text = ConvertTo-DiagnosisText $errorText
        if (-not $text) {
            continue
        }
        $item = [ordered]@{
            id = 'analysis.step_failed'
            source_type = 'analysis_error'
            severity = 'blocking'
            category = 'analysis'
            title = 'Analysis step failed'
            likely_cause = 'The diagnostic helper could not complete one of its analysis steps.'
            suggested_fix = 'Review the raw error below, then rerun collection after fixing the missing input or environment issue.'
            raw_errors = @([ordered]@{ text = (Limit-DiagnosisText -Text $text) })
        }
        $key = "{0}|{1}|{2}" -f $item.source_type, $item.id, $item.raw_errors[0].text
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $items.Add($item) | Out-Null
        }
    }
    return @($items.ToArray())
}

function Get-DiagnosisRawErrorText {
    param([object]$RawError)
    foreach ($name in @('message', 'text', 'raw_context')) {
        $text = Get-ObjectText -Object $RawError -Name $name
        if ($text) {
            return (Limit-DiagnosisText -Text $text -MaxLength 700)
        }
    }
    return ''
}

function Test-DiagnosisImportDependencyEvidence {
    param(
        [object]$Item,
        [string]$Evidence
    )
    $id = Get-ObjectText -Object $Item -Name 'id'
    $errorIds = @((Get-ObjectProperty -Object $Item -Name 'error_ids') | ForEach-Object {
        ConvertTo-DiagnosisText $_
    }) -join ' '
    $text = "$id $errorIds $Evidence"
    return ($text -match '(?i)\bVX_IMP_[A-Z0-9_]+\b|referenced by at least one of the imported components|not part of the import file|imported components')
}

function Get-ScriptErrorSignature {
    param([string]$Message)
    $normalized = $Message -replace '\s*<Log\b.*$', ''
    $normalized = $normalized -replace '\]\]></Exception>.*$', ''
    $normalized = $normalized -replace '\s+at\s+[A-Za-z0-9_.]+\(.*$', ''
    $normalized = ($normalized -replace '\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?', '') -replace '\s+', ' '
    $sentences = @(
        $normalized.Trim() -split '(?<=[.!?])\s+' |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $uniqueSentences = New-Object 'System.Collections.Generic.List[string]'
    foreach ($sentence in $sentences) {
        $candidate = $sentence.Trim()
        $alreadyPresent = $false
        foreach ($existing in $uniqueSentences) {
            if ([string]::Equals($existing, $candidate, [System.StringComparison]::OrdinalIgnoreCase)) {
                $alreadyPresent = $true
                break
            }
        }
        if (-not $alreadyPresent) {
            $uniqueSentences.Add($candidate) | Out-Null
        }
    }
    return ($uniqueSentences -join ' ').Trim().ToLowerInvariant()
}

function Get-ScriptEditorDumpDiagnosisItems {
    param([string]$DestinationRoot)
    $path = Join-Path $DestinationRoot 'script_editor_dump_scan\script_editor_dump_scan.json'
    $scan = Read-DiagnosisJson -Path $path
    if ($null -eq $scan) {
        return @()
    }
    $items = New-Object 'System.Collections.Generic.List[object]'
    foreach ($finding in @($scan.findings)) {
        $rawErrors = @($finding.files | ForEach-Object {
            [ordered]@{
                file = $_
                source = 'VisionX crash dump'
                text = $finding.actual_error
            }
        })
        $items.Add([ordered]@{
            id = $finding.id
            source_type = 'script_editor_dump'
            severity = $finding.severity
            category = $finding.category
            title = $finding.title
            likely_cause = $finding.likely_cause
            suggested_fix = $finding.suggested_fix
            raw_errors = $rawErrors
        }) | Out-Null
    }
    return @($items.ToArray())
}

function Get-FluentControlInfopadDiagnosisItems {
    param([string]$DestinationRoot)

    $path = Join-Path $DestinationRoot 'fluentcontrol_infopad\fluentcontrol_infopad.json'
    $capture = Read-DiagnosisJson -Path $path
    if ($null -eq $capture -or $capture.status -ne 'captured') {
        return @()
    }
    $items = New-Object 'System.Collections.Generic.List[object]'
    $index = 0
    foreach ($entry in @($capture.messages)) {
        $message = Get-ObjectText -Object $entry -Name 'message'
        if (-not $message) {
            continue
        }
        $index++
        $commandNumber = Get-ObjectText -Object $entry -Name 'command_number'
        $items.Add([ordered]@{
            id = "fluentcontrol_infopad.$index"
            source_type = 'fluentcontrol_infopad'
            severity = 'error'
            category = 'script'
            title = 'FluentControl embedded Infopad error'
            likely_cause = 'FluentControl rejected the validation state of the displayed script command.'
            suggested_fix = 'Use the command number and message below to correct the command in Script Editor, then reopen the script.'
            raw_errors = @([ordered]@{
                source = 'FluentControl embedded Infopad'
                file = $path
                script = 'FluentControl Infopad'
                script_line = $commandNumber
                command_hint = $commandNumber
                message = $message
            })
        }) | Out-Null
    }
    return @($items.ToArray())
}

function Get-ScriptErrorGroups {
    param([object[]]$Items)
    $groups = [ordered]@{}
    foreach ($item in @($Items)) {
        if ((Get-ObjectText -Object $item -Name 'source_type') -notin @('log_diagnostic', 'script_editor_dump', 'fluentcontrol_infopad')) {
            continue
        }
        $itemScripts = @(
            @($item.raw_errors) |
                ForEach-Object { Get-ObjectText -Object $_ -Name 'script' } |
                Where-Object { $_ }
        )
        foreach ($rawError in @($item.raw_errors)) {
            $message = Get-DiagnosisRawErrorText -RawError $rawError
            if (-not $message) {
                continue
            }
            $script = Get-ObjectText -Object $rawError -Name 'script'
            if (-not $script -and $itemScripts.Count -gt 0) {
                continue
            }
            if (-not $script) {
                $script = 'Unattributed Script/Runtime Errors'
            }
            if (-not $groups.Contains($script)) {
                $groups[$script] = [ordered]@{
                    script = $script
                    issues = New-Object 'System.Collections.Generic.List[object]'
                    seen = @{}
                }
            }
            # FluentControl may emit one diagnostic many times while a command
            # unwinds, and one message can match multiple classifiers. Present
            # that normalized error once per named script.
            $key = Get-ScriptErrorSignature -Message $message
            if ($groups[$script].seen.ContainsKey($key)) {
                continue
            }
            $groups[$script].seen[$key] = $true
            $groups[$script].issues.Add([ordered]@{
                id = Get-ObjectText -Object $item -Name 'id'
                severity = Get-ObjectText -Object $item -Name 'severity'
                title = Get-ObjectText -Object $item -Name 'title'
                actual_error = $message
                likely_cause = Get-ObjectText -Object $item -Name 'likely_cause'
                potential_fix = Get-ObjectText -Object $item -Name 'suggested_fix'
                main_script = Get-ObjectText -Object $rawError -Name 'main_script'
                script_line = Get-ObjectText -Object $rawError -Name 'script_line'
                script_source = Get-ObjectText -Object $rawError -Name 'script_source'
                command_hint = Get-ObjectText -Object $rawError -Name 'command_hint'
                raw_error = $rawError
            }) | Out-Null
        }
    }
    $output = New-Object 'System.Collections.Generic.List[object]'
    foreach ($group in @($groups.Values | Sort-Object @{ Expression = {
        if ($_.script -eq 'Unattributed Script/Runtime Errors') { 1 } else { 0 }
    } }, @{ Expression = { $_.script } })) {
        $output.Add([ordered]@{
            script = $group.script
            issues = @($group.issues.ToArray())
        }) | Out-Null
    }
    return @($output.ToArray())
}

function Get-ImportScanDiagnosisItems {
    param([string]$DestinationRoot)
    $path = Join-Path $DestinationRoot 'import_scan\import_error_scan.json'
    $scan = Read-DiagnosisJson -Path $path
    if ($null -eq $scan) {
        return @()
    }
    $items = New-Object 'System.Collections.Generic.List[object]'
    foreach ($finding in @($scan.findings)) {
        if ([int]$finding.count -le 0) {
            continue
        }
        $rawErrors = @($finding.matches | ForEach-Object {
            [ordered]@{
                file = $_.file
                line = $_.line
                source = $_.source_kind
                text = $_.text
            }
        })
        $items.Add([ordered]@{
            id = "import_scan.$($finding.id)"
            source_type = 'import_scan'
            severity = 'high'
            category = 'import'
            title = $finding.title
            likely_cause = 'The collected import, audit, or ULF evidence matches this import failure pattern.'
            suggested_fix = 'Review the listed log evidence, correct the named dependency or archive condition, then retry the FluentControl import.'
            raw_errors = $rawErrors
        }) | Out-Null
    }
    return @($items.ToArray())
}

function Test-DiagnosisRuntimeMissingFileEvidence {
    param([string]$Evidence)
    return ($Evidence -match '(?i)Failed to open|FileNotFoundException|DirectoryNotFoundException|Could not find file|Could not find a part of the path|Cannot find path|could not open|cannot open')
}

function Get-DiagnosisSectionId {
    param([object]$Item)
    $sourceType = Get-ObjectText -Object $Item -Name 'source_type'
    $category = Get-ObjectText -Object $Item -Name 'category'
    $evidence = @($Item.raw_errors | ForEach-Object { Get-DiagnosisRawErrorText -RawError $_ }) -join ' '
    if ($sourceType -eq 'import_scan' -or $category -in @('import', 'checksum', 'subroutine')) {
        return 'import-errors'
    }
    # Runtime missing-file evidence stays in script-errors even when the same
    # classifier bucket also carries VX_IMP_* import-dialog noise.
    if ($category -eq 'dependencies' -and (Test-DiagnosisRuntimeMissingFileEvidence -Evidence $evidence)) {
        return 'script-errors'
    }
    if ($category -eq 'dependencies' -and (Test-DiagnosisImportDependencyEvidence -Item $Item -Evidence $evidence)) {
        return 'import-errors'
    }
    if ($category -eq 'crash' -or $evidence -match '(?i)\b(crash|dump|fatal|unhandled exception|application error|windows error reporting)\b') {
        return 'program-crash'
    }
    return 'script-errors'
}

function Get-DiagnosisSections {
    param(
        [object[]]$Items,
        [string]$ProfileName
    )
    $definitions = @(
        [ordered]@{ id = 'script-errors'; label = 'In-Script errors' },
        [ordered]@{ id = 'program-crash'; label = 'Tecan Program Crash' },
        [ordered]@{ id = 'import-errors'; label = 'Import errors' }
    )
    $requestedIds = if ($ProfileName -eq 'everything') {
        @('script-errors', 'program-crash', 'import-errors')
    } else {
        @($ProfileName)
    }
    $sections = New-Object 'System.Collections.Generic.List[object]'
    foreach ($definition in $definitions) {
        if ($definition.id -notin $requestedIds) {
            continue
        }
        $sectionItems = @($Items | Where-Object { (Get-DiagnosisSectionId -Item $_) -eq $definition.id })
        $sections.Add([ordered]@{
            id = $definition.id
            label = $definition.label
            items = $sectionItems
            script_errors = @(Get-ScriptErrorGroups -Items $sectionItems)
        }) | Out-Null
    }
    return @($sections.ToArray())
}

function Read-DiagnosisJson {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return [ordered]@{
            summary = [ordered]@{ errors = @("Could not read analyzer JSON ${Path}: $($_.Exception.Message)") }
        }
    }
}

function Format-RawErrorMarkdown {
    param([object]$RawError)
    $parts = @()
    foreach ($name in @('source', 'file')) {
        $value = Get-ObjectText -Object $RawError -Name $name
        if ($value) {
            $parts += $value
            break
        }
    }
    $line = Get-ObjectText -Object $RawError -Name 'line'
    if ($line) {
        $parts += "line $line"
    }
    $scriptLine = Get-ObjectText -Object $RawError -Name 'script_line'
    if ($scriptLine) {
        $parts += "script line $scriptLine"
    }
    $level = Get-ObjectText -Object $RawError -Name 'level'
    if ($level) {
        $parts += $level
    }
    $errorId = Get-ObjectText -Object $RawError -Name 'error_id'
    if ($errorId) {
        $parts += $errorId
    }
    $message = Get-ObjectText -Object $RawError -Name 'message'
    if (-not $message) {
        $message = Get-ObjectText -Object $RawError -Name 'text'
    }
    if (-not $message) {
        $message = Get-ObjectText -Object $RawError -Name 'raw_context'
    }
    $message = Limit-DiagnosisText -Text $message -MaxLength 700
    $prefix = ''
    if ($parts.Count -gt 0) {
        $prefix = '[' + ($parts -join ' | ') + '] '
    }
    return "- $prefix$message"
}

function Get-DiagnosisItemRawErrorSignatures {
    param([object]$Item)
    $signatures = New-Object 'System.Collections.Generic.List[string]'
    foreach ($rawError in @($Item.raw_errors)) {
        $message = Get-DiagnosisRawErrorText -RawError $rawError
        if (-not $message) {
            continue
        }
        $signature = Get-ScriptErrorSignature -Message $message
        if ($signature) {
            $signatures.Add($signature) | Out-Null
        }
    }
    return @($signatures.ToArray())
}

function Get-UniqueRawErrorsBySignature {
    param([object[]]$RawErrors)
    $seen = @{}
    $unique = New-Object 'System.Collections.Generic.List[object]'
    foreach ($rawError in @($RawErrors)) {
        $message = Get-DiagnosisRawErrorText -RawError $rawError
        $signature = if ($message) { Get-ScriptErrorSignature -Message $message } else { '' }
        if (-not $signature) {
            $signature = Format-RawErrorMarkdown -RawError $rawError
        }
        if ($seen.ContainsKey($signature)) {
            continue
        }
        $seen[$signature] = $true
        $unique.Add($rawError) | Out-Null
    }
    return @($unique.ToArray())
}

function Render-HumanDiagnosisMarkdown {
    param([object]$Diagnosis)
    $summary = $Diagnosis.summary
    $sources = $Diagnosis.sources
    $lines = @(
        '# Diagnosis',
        '',
        "Status: ``$($Diagnosis.status)``",
        "Generated: ``$($Diagnosis.generated_at)``",
        "Profile: ``$($Diagnosis.profile)``",
        '',
        '## Summary',
        '',
        "- Findings: ``$($summary.finding_count)``",
        "- Raw error entries: ``$($summary.raw_error_count)``"
    )
    if ($Diagnosis.notes.Count -gt 0) {
        foreach ($note in $Diagnosis.notes) {
            $lines += "- $note"
        }
    }
    $lines += ''
    $lines += '## Sources'
    $lines += ''
    $lines += "- Log: ``$($sources.ulf)``"
    if ($sources.zeia) {
        $lines += "- ZEIA: ``$($sources.zeia)``"
    }
    if ($sources.cli_root) {
        $lines += "- Analyzer: ``$($sources.cli_root)``"
    }
    foreach ($section in @($Diagnosis.sections)) {
        $lines += ''
        $lines += "## $($section.label)"
        $lines += ''
        $sectionScriptErrors = @($section.script_errors)
        $shownScriptIssueIds = @{}
        $shownScriptIssueSignatures = @{}
        foreach ($scriptGroup in $sectionScriptErrors) {
            foreach ($issue in @($scriptGroup.issues)) {
                $issueId = Get-ObjectText -Object $issue -Name 'id'
                if ($issueId) {
                    $shownScriptIssueIds[$issueId] = $true
                }
                $message = Get-ObjectText -Object $issue -Name 'actual_error'
                if ($message) {
                    $signature = Get-ScriptErrorSignature -Message $message
                    if ($signature) {
                        $shownScriptIssueSignatures[$signature] = $true
                    }
                }
            }
        }
        $otherItems = @($section.items | Where-Object {
            $itemId = Get-ObjectText -Object $_ -Name 'id'
            if ($itemId -and $shownScriptIssueIds.ContainsKey($itemId)) {
                return $false
            }
            foreach ($signature in @(Get-DiagnosisItemRawErrorSignatures -Item $_)) {
                if ($shownScriptIssueSignatures.ContainsKey($signature)) {
                    return $false
                }
            }
            return ((Get-ObjectText -Object $_ -Name 'source_type') -ne 'log_diagnostic' -or @($_.raw_errors).Count -eq 0)
        })
        if ($sectionScriptErrors.Count -eq 0 -and $otherItems.Count -eq 0) {
            $lines += "No $($section.label.ToLowerInvariant()) findings were detected."
            continue
        }
        foreach ($scriptGroup in $sectionScriptErrors) {
            $lines += "### Script: ``$($scriptGroup.script)``"
            $lines += ''
            $issueNumber = 0
            foreach ($issue in @($scriptGroup.issues)) {
                $issueNumber++
                $lines += "#### $issueNumber. $($issue.title)"
                if ($issue.severity) { $lines += "- Severity: ``$($issue.severity)``" }
                if ($issue.main_script -and $issue.main_script -ne $scriptGroup.script) {
                    $lines += "- Main script: ``$($issue.main_script)``"
                }
                if ($issue.script_line) { $lines += "- Script line: ``$($issue.script_line)``" }
                if ($issue.command_hint) { $lines += "- Command hint: ``$($issue.command_hint)``" }
                if ($issue.script_source) { $lines += "- Script link: ``$($issue.script_source)``" }
                $lines += "- Actual error: $($issue.actual_error)"
                if ($issue.likely_cause) { $lines += "- Potential cause: $($issue.likely_cause)" }
                if ($issue.potential_fix) { $lines += "- Potential fix: $($issue.potential_fix)" }
                if ($issue.raw_error) {
                    $lines += '- Evidence:'
                    $lines += (Format-RawErrorMarkdown -RawError $issue.raw_error)
                }
                $lines += ''
            }
        }
        $number = 0
        foreach ($item in $otherItems) {
            $number++
            $lines += "### Other Error $number. $($item.title)"
            $lines += ''
            $severity = Get-ObjectText -Object $item -Name 'severity'
            $category = Get-ObjectText -Object $item -Name 'category'
            if ($severity) { $lines += "- Severity: ``$severity``" }
            if ($category) { $lines += "- Category: ``$category``" }
            if ($item.likely_cause) { $lines += "- Potential cause: $($item.likely_cause)" }
            if ($item.suggested_fix) { $lines += "- Potential fix: $($item.suggested_fix)" }
            $uniqueRawErrors = @(Get-UniqueRawErrorsBySignature -RawErrors @($item.raw_errors))
            if ($uniqueRawErrors.Count -gt 0) {
                $lines += '- Evidence:'
            }
            foreach ($rawError in $uniqueRawErrors) {
                $lines += Format-RawErrorMarkdown -RawError $rawError
            }
            $lines += ''
        }
    }
    return ($lines -join "`r`n") + "`r`n"
}

function Write-HumanReadableDiagnosis {
    param(
        [string]$DestinationRoot,
        [string]$Status,
        [string]$ProfileName,
        [string]$UlfPath,
        [string]$ZeiaPath,
        [string]$CliRoot,
        [object]$Report,
        [string[]]$Notes = @()
    )
    $items = @(
        @(Get-DiagnosisItemsFromReport -Report $Report) +
        @(Get-ImportScanDiagnosisItems -DestinationRoot $DestinationRoot) +
        @(Get-ScriptEditorDumpDiagnosisItems -DestinationRoot $DestinationRoot) +
        @(Get-FluentControlInfopadDiagnosisItems -DestinationRoot $DestinationRoot)
    )
    $scriptErrors = Get-ScriptErrorGroups -Items $items
    $sections = Get-DiagnosisSections -Items $items -ProfileName $ProfileName
    $rawErrorCount = 0
    $scriptErrorCount = 0
    foreach ($item in @($items)) {
        $rawErrorCount += @($item.raw_errors).Count
    }
    foreach ($scriptErrorGroup in @($scriptErrors)) {
        $scriptErrorCount += @($scriptErrorGroup.issues).Count
    }
    $diagnosis = [ordered]@{
        schema_version = 'tecan.diagnosis_results.v1'
        generated_at = (Get-Date).ToString('o')
        status = $Status
        profile = $ProfileName
        sources = [ordered]@{
            ulf = $UlfPath
            zeia = $ZeiaPath
            cli_root = $CliRoot
            fluentcontrol_infopad = (Join-Path $DestinationRoot 'fluentcontrol_infopad\fluentcontrol_infopad.json')
        }
        summary = [ordered]@{
            finding_count = @($items).Count
            raw_error_count = $rawErrorCount
            script_count = @($scriptErrors).Count
            script_error_count = $scriptErrorCount
        }
        notes = @($Notes)
        items = @($items)
        script_errors = @($scriptErrors)
        sections = @($sections)
    }
    $mdPath = Join-Path $DestinationRoot 'diagnosis.md'
    $jsonPath = Join-Path $DestinationRoot 'diagnosis.json'
    # Windows PowerShell's Out-File -Encoding utf8 adds a BOM; keep diagnostic
    # artifacts plain UTF-8 so standard JSON readers can consume them directly.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($mdPath, (Render-HumanDiagnosisMarkdown -Diagnosis $diagnosis), $utf8NoBom)
    [System.IO.File]::WriteAllText($jsonPath, ($diagnosis | ConvertTo-Json -Depth 12), $utf8NoBom)
    return [ordered]@{
        markdown = $mdPath
        json = $jsonPath
        item_count = @($items).Count
        raw_error_count = $rawErrorCount
    }
}

function Remove-LikelyCauseIntermediates {
    param([string]$AnalysisOut)
    if (-not (Test-Path -LiteralPath $AnalysisOut -PathType Container)) {
        return
    }
    Get-ChildItem -LiteralPath $AnalysisOut -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'status.txt' } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

function Invoke-LikelyCauseAnalysis {
    param(
        [string]$DestinationRoot,
        [string]$BundleRoot,
        [int]$MaxRecords = 200,
        [string]$ProfileName = ''
    )
    $analysisOut = Join-Path $DestinationRoot 'likely_causes'
    $statusPath = Join-Path $analysisOut 'status.txt'
    New-Item -ItemType Directory -Force -Path $analysisOut | Out-Null
    $ulf = Get-ChildItem -LiteralPath $DestinationRoot -Recurse -File -Filter '*.ulf' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $zeia = Get-ChildItem -LiteralPath $BundleRoot -File -Filter '*.zeia' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $cliRoot = Resolve-ProtocolBuilderRoot -BundleRoot $BundleRoot
    $python = Resolve-PythonExe -CliRoot $cliRoot
    $ulfPath = if ($ulf) { $ulf.FullName } else { '' }
    $zeiaPath = if ($zeia) { $zeia.FullName } else { '' }
    $messages = @()
    $messages += "CLI root: $cliRoot"
    $messages += "Python: $python"
    $messages += "ZEIA: $zeiaPath"
    $messages += "ULF: $ulfPath"
    if (-not $ulf) {
        $messages += 'No ULF log was collected; cannot run likely-cause log analysis.'
        $diagnosis = Write-HumanReadableDiagnosis -DestinationRoot $DestinationRoot -Status 'no_log' -ProfileName $ProfileName -UlfPath $ulfPath -ZeiaPath $zeiaPath -CliRoot $cliRoot -Report $null -Notes @('No ULF log was collected, so likely-cause analysis could not inspect runtime errors.')
        $messages += "Root diagnosis.md: $($diagnosis.markdown)"
        $messages += "Root diagnosis.json: $($diagnosis.json)"
        $messages | Out-File -LiteralPath $statusPath -Encoding utf8
        return @{ status = 'no_log'; status_file = $statusPath; diagnosis = $diagnosis }
    }
    if (-not $cliRoot) {
        $messages += 'Protocol-builder CLI was not found beside this bundle; copied logs only.'
        $diagnosis = Write-HumanReadableDiagnosis -DestinationRoot $DestinationRoot -Status 'cli_missing' -ProfileName $ProfileName -UlfPath $ulfPath -ZeiaPath $zeiaPath -CliRoot $cliRoot -Report $null -Notes @('Protocol-builder CLI was not found beside this bundle, so the logs were collected without automated diagnosis.')
        $messages += "Root diagnosis.md: $($diagnosis.markdown)"
        $messages += "Root diagnosis.json: $($diagnosis.json)"
        $messages | Out-File -LiteralPath $statusPath -Encoding utf8
        return @{ status = 'cli_missing'; status_file = $statusPath; diagnosis = $diagnosis }
    }
    Push-Location $cliRoot
    try {
        $auditArgs = @()
        $auditRoot = Join-Path $DestinationRoot 'visionx_audit_trail'
        if (Test-Path -LiteralPath $auditRoot -PathType Container) {
            foreach ($auditLog in Get-ChildItem -LiteralPath $auditRoot -File -Filter '*.csv' -ErrorAction SilentlyContinue) {
                $auditArgs += @('--audit-log', $auditLog.FullName)
            }
        }
        if ($zeia) {
            $cliArgs = @('-m', 'fluent_pipeline.cli', 'analyze', $zeia.FullName, '--log', $ulf.FullName, '--out-dir', $analysisOut, '--max-records', ([string]$MaxRecords)) + $auditArgs
        } else {
            $parseOut = Join-Path $analysisOut 'diagnostics'
            New-Item -ItemType Directory -Force -Path $parseOut | Out-Null
            $cliArgs = @('-m', 'fluent_pipeline.cli', 'parse-fluent-log', $ulf.FullName, '--report', (Join-Path $parseOut 'diagnosis.md'), '--json-out', (Join-Path $parseOut 'diagnosis.json')) + $auditArgs
        }
        $messages += "Command: $python $($cliArgs -join ' ')"
        $output = & $python @cliArgs 2>&1
        $exitCode = $LASTEXITCODE
        $messages += "Exit code: $exitCode"
        $messages += $output
    } finally {
        Pop-Location
    }
    $diagMd = Join-Path $analysisOut 'diagnostics\diagnosis.md'
    $diagJson = Join-Path $analysisOut 'diagnostics\diagnosis.json'
    $analysisJson = Join-Path $analysisOut 'analysis.json'
    $report = $null
    if (Test-Path -LiteralPath $analysisJson -PathType Leaf) {
        $report = Read-DiagnosisJson -Path $analysisJson
        $messages += 'Root diagnosis source: transformed likely_causes\analysis.json'
    } elseif (Test-Path -LiteralPath $diagJson -PathType Leaf) {
        $report = Read-DiagnosisJson -Path $diagJson
        $messages += 'Root diagnosis source: transformed likely_causes\diagnostics\diagnosis.json'
    } elseif (Test-Path -LiteralPath $diagMd -PathType Leaf) {
        $messages += 'Analyzer wrote markdown but no JSON; root diagnosis will record the analyzer status only.'
    }
    $status = 'failed'
    if ($exitCode -eq 0) {
        $status = 'complete'
    }
    $notes = @()
    if ($exitCode -ne 0) {
        $notes += 'Analyzer command did not exit cleanly. Review likely_causes\status.txt for command output.'
    }
    $diagnosis = Write-HumanReadableDiagnosis -DestinationRoot $DestinationRoot -Status $status -ProfileName $ProfileName -UlfPath $ulfPath -ZeiaPath $zeiaPath -CliRoot $cliRoot -Report $report -Notes $notes
    $messages += "Root diagnosis.md: $($diagnosis.markdown)"
    $messages += "Root diagnosis.json: $($diagnosis.json)"
    $messages += "Diagnosis items: $($diagnosis.item_count)"
    $messages += "Raw error entries: $($diagnosis.raw_error_count)"
    Remove-LikelyCauseIntermediates -AnalysisOut $analysisOut
    $messages | Out-File -LiteralPath $statusPath -Encoding utf8
    return @{ status = $status; status_file = $statusPath; output_dir = $analysisOut; diagnosis = $diagnosis }
}

$profileName = Normalize-LogProfile $Profile
if ($SinceDays -le 0 -and $env:SETUP_LOG_DAYS) {
    $SinceDays = [int]$env:SETUP_LOG_DAYS
}
if ($LikelyCauseMaxRecords -le 0 -and $env:SETUP_LIKELY_CAUSE_MAX_RECORDS) {
    $LikelyCauseMaxRecords = [int]$env:SETUP_LIKELY_CAUSE_MAX_RECORDS
}
if ($EventLogMaxEvents -le 0 -and $env:SETUP_WINDOWS_EVENT_MAX_EVENTS) {
    $EventLogMaxEvents = [int]$env:SETUP_WINDOWS_EVENT_MAX_EVENTS
}
if ($LikelyCauseMaxRecords -le 0) {
    $LikelyCauseMaxRecords = 200
}
if ($EventLogMaxEvents -le 0) {
    $EventLogMaxEvents = 2000
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($BundleRoot)) {
    $BundleRoot = $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$BundleRoot = [IO.Path]::GetFullPath($BundleRoot)

$rootSpecs = @{
    loggingserver_logfiles = New-RootSpec 'loggingserver_logfiles' @((Join-Path $env:ProgramData 'Tecan\LoggingServer\LogFiles')) @('*.ulf', '*.log', '*.txt')
    visionx_audit_trail = New-RootSpec 'visionx_audit_trail' @((Join-Path $env:ProgramData 'Tecan\VisionX\AuditTrail')) @('AuditTrail_*.csv', '*.csv', '*.log', '*.txt')
    visionx_audit_archive = New-RootSpec 'visionx_audit_archive' @((Join-Path $env:ProgramData 'Tecan\VisionX\AuditArch\Log')) @('auditarch*.log', '*.log', '*.txt')
    visionx_task_handling = New-RootSpec 'visionx_task_handling' @((Join-Path $env:ProgramData 'Tecan\VisionX\TaskHandling')) @('*.log', '*.txt', '*.ulf', '*.xml')
    datastore_iot_client_logs = New-RootSpec 'datastore_iot_client_logs' @(
        (Join-Path $env:ProgramData 'Tecan\DataStore\IoT-Client\MAP.Services.Logging.Service\LogFile'),
        (Join-Path $env:ProgramData 'Tecan\VisionX\DataStoreIoTClient')
    ) @('*.ulf', '*.log', '*.txt')
    visionx_logs = New-RootSpec 'visionx_logs' @((Join-Path $env:ProgramData 'Tecan\VisionX\Logs')) @('*.ulf', '*.log', '*.txt', '*.csv')
    visionx_logfiles = New-RootSpec 'visionx_logfiles' @((Join-Path $env:ProgramData 'Tecan\VisionX\LogFiles')) @('*.ulf', '*.log', '*.txt', '*.csv')
    fluentcontrol_programdata_logs = New-RootSpec 'fluentcontrol_programdata_logs' @((Join-Path $env:ProgramData 'Tecan\FluentControl')) @('*.ulf', '*.log', '*.txt', '*.csv')
}

$profiles = @{
    everything = @{
        Label = 'Everything'
        Roots = @('loggingserver_logfiles', 'visionx_audit_trail', 'visionx_audit_archive', 'visionx_task_handling', 'datastore_iot_client_logs', 'visionx_logs', 'visionx_logfiles', 'fluentcontrol_programdata_logs')
        BundleContext = $true
        DumpListing = $true
        EventLog = $true
        ImportScan = $true
        ScriptEditorDumpScan = $true
        FluentControlInfopad = $true
        SinceDays = 1
    }
    'script-errors' = @{
        Label = 'In-Script errors'
        Roots = @('loggingserver_logfiles', 'visionx_audit_trail', 'visionx_task_handling')
        BundleContext = $true
        DumpListing = $false
        EventLog = $false
        ImportScan = $false
        ScriptEditorDumpScan = $true
        FluentControlInfopad = $true
        SinceDays = 1
    }
    'program-crash' = @{
        Label = 'Tecan Program Crash'
        Roots = @('loggingserver_logfiles', 'visionx_task_handling', 'datastore_iot_client_logs', 'visionx_logs', 'visionx_logfiles')
        BundleContext = $false
        DumpListing = $true
        EventLog = $true
        ImportScan = $false
        ScriptEditorDumpScan = $false
        SinceDays = 1
    }
    'import-errors' = @{
        Label = 'Import errors'
        Roots = @('loggingserver_logfiles', 'visionx_audit_trail', 'visionx_audit_archive', 'datastore_iot_client_logs')
        BundleContext = $true
        DumpListing = $false
        EventLog = $false
        ImportScan = $true
        ScriptEditorDumpScan = $false
        SinceDays = 1
    }
}

$spec = $profiles[$profileName]
if ($SinceDays -gt 0) {
    $spec.SinceDays = $SinceDays
}
$dest = New-VersionedErrorLogOutputPath -OutputRoot $OutputRoot
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Write-Host ""
Write-Host "==== Collect diagnostic logs: $($spec.Label) ===="
Write-Host "Output: $dest"
Write-Host "Progress bars update while copying. Long scans are normal."

$manifest = [ordered]@{
    schema_version = 'tecan.diagnostic_log_bundle.v1'
    generated_at = (Get-Date).ToString('o')
    profile = $profileName
    label = $spec.Label
    bundle_root = $BundleRoot
    output = $dest
    settings = [ordered]@{
        since_days = $spec.SinceDays
        likely_cause_max_records = $LikelyCauseMaxRecords
        event_log_max_events = $EventLogMaxEvents
    }
    copied_roots = @()
    bundle_context = $null
    dumpfiles = $null
    event_log = $null
    import_error_scan = $null
    script_editor_dump_scan = $null
    fluentcontrol_infopad = $null
    likely_causes = $null
}

$rootTotal = @($spec.Roots).Count
$rootIndex = 0
foreach ($rootName in $spec.Roots) {
    $rootIndex++
    Write-Host ""
    Write-Host ("---- Step {0}/{1}: copy root '{2}' ----" -f $rootIndex, $rootTotal, $rootName)
    $manifest.copied_roots += Copy-RecentRoot -RootSpec $rootSpecs[$rootName] -DestinationRoot $dest -SinceDays $spec.SinceDays
}
if ($spec.BundleContext) {
    Write-Host ""
    Write-Host "---- Collecting bundle context evidence ----"
    $manifest.bundle_context = Copy-BundleEvidence -SourceRoot $BundleRoot -DestinationRoot $dest -ProfileName $profileName
}
if ($spec.DumpListing) {
    Write-Host ""
    Write-Host "---- Listing dump files ----"
    $manifest.dumpfiles = Write-DumpListing -DestinationRoot $dest -SinceDays $spec.SinceDays
}
if ($spec.EventLog) {
    Write-Host ""
    Write-Host "---- Collecting Windows event log evidence ----"
    $manifest.event_log = Write-EventLogEvidence -DestinationRoot $dest -SinceDays $spec.SinceDays -MaxEvents $EventLogMaxEvents
}
if ($spec.ImportScan) {
    Write-Host ""
    Write-Host "---- Scanning import-error signatures ----"
    $manifest.import_error_scan = Write-ImportErrorScan -DestinationRoot $dest
}
if ($spec.ScriptEditorDumpScan) {
    Write-Host ""
    Write-Host "---- Scanning Script Editor dump evidence ----"
    $manifest.script_editor_dump_scan = Write-ScriptEditorDumpScan -DestinationRoot $dest -SinceDays $spec.SinceDays -BundleRoot $BundleRoot
}
if ($CaptureFluentControlInfopad -and $spec.FluentControlInfopad) {
    Write-Host ""
    Write-Host "---- Capturing FluentControl Infopad ----"
    $manifest.fluentcontrol_infopad = Write-FluentControlInfopadEvidence -DestinationRoot $dest
}
Write-Host ""
Write-Host "---- Building likely-cause analysis ----"
$manifest.likely_causes = Invoke-LikelyCauseAnalysis -DestinationRoot $dest -BundleRoot $BundleRoot -MaxRecords $LikelyCauseMaxRecords -ProfileName $profileName

$metadataOutput = Join-Path $dest 'metadata'
New-Item -ItemType Directory -Force -Path $metadataOutput | Out-Null
$manifestPath = Join-Path $metadataOutput 'collection_manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $manifestPath -Encoding utf8
$summaryPath = Join-Path $metadataOutput 'collection_manifest.txt'
@(
    "Profile: $($spec.Label)",
    "Profile id: $profileName",
    "Bundle root: $BundleRoot",
    "Generated: $($manifest.generated_at)",
    "Log lookback days: $($manifest.settings.since_days)",
    "Likely-cause max log records: $LikelyCauseMaxRecords",
    "Windows event max records: $EventLogMaxEvents",
    "Output: $dest",
    "Manifest: $manifestPath"
) | Out-File -LiteralPath $summaryPath -Encoding utf8

Write-Host ""
Write-Host "Collected logs: $dest"
