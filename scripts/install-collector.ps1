<#
.SYNOPSIS
    Install PowerMesh Collector on the central node.
.DESCRIPTION
    Creates a Python venv, installs dependencies, copies config,
    and registers a scheduled task for the collector HTTP server.
#>
param(
    [string]$InstallDir = "$env:ProgramData\PowerMesh-Collector",
    [string]$ConfigSource = "",
    [int]$Port = 8430
)

$ErrorActionPreference = "Stop"

Write-Host "=== PowerMesh Collector Installer ===" -ForegroundColor Cyan

# 1. Create install directory
if (!(Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# 2. Copy project files
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Resolve
Write-Host "Copying from $projectRoot..."
Copy-Item "$projectRoot\src" "$InstallDir\src" -Recurse -Force
Copy-Item "$projectRoot\pyproject.toml" "$InstallDir\" -Force
Copy-Item "$projectRoot\requirements.txt" "$InstallDir\" -Force

# 3. Create venv and install
Write-Host "Creating Python virtual environment..."
python -m venv "$InstallDir\.venv"
& "$InstallDir\.venv\Scripts\pip.exe" install --upgrade pip -q
& "$InstallDir\.venv\Scripts\pip.exe" install -r "$InstallDir\requirements.txt" -q
Write-Host "Dependencies installed" -ForegroundColor Green

# 4. Config
$configDir = "$InstallDir\config"
New-Item -ItemType Directory -Path $configDir -Force | Out-Null

if ($ConfigSource -and (Test-Path $ConfigSource)) {
    Copy-Item $ConfigSource "$configDir\mesh.yaml" -Force
    Write-Host "Config copied from $ConfigSource"
}
elseif (!(Test-Path "$configDir\mesh.yaml")) {
    Copy-Item "$projectRoot\config\mesh.yaml" "$configDir\mesh.yaml" -Force
    Write-Host "Default config copied — edit $configDir\mesh.yaml before starting"
}

# 5. Data directory
New-Item -ItemType Directory -Path "$InstallDir\data" -Force | Out-Null

# 6. Create run script
$runScript = @"
Set-Location "$InstallDir"
& "$InstallDir\.venv\Scripts\python.exe" -m src.collector "$InstallDir\config\mesh.yaml"
"@
Set-Content "$InstallDir\run-collector.ps1" $runScript

# 7. Create firewall rule for the port
$fwRuleName = "PowerMesh-Collector-$Port"
$existingRule = Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue
if (!$existingRule) {
    New-NetFirewallRule `
        -DisplayName $fwRuleName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Action Allow `
        -Profile Private | Out-Null
    Write-Host "Firewall rule created for port $Port (Private profile)"
}

# 8. Register scheduled task
$taskName = "PowerMesh-Collector"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing scheduled task"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$InstallDir\run-collector.ps1`"" `
    -WorkingDirectory $InstallDir

$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "PowerMesh central power data collector" `
    -RunLevel Highest | Out-Null

Write-Host "`nInstalled to: $InstallDir" -ForegroundColor Green
Write-Host "Scheduled task: $taskName (runs at startup)" -ForegroundColor Green
Write-Host "Listening on: http://0.0.0.0:$Port" -ForegroundColor Green
Write-Host "Dashboard at: http://localhost:$Port/" -ForegroundColor Green
Write-Host "`nTo start now:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "To uninstall:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
