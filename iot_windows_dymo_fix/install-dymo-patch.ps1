<#
.SYNOPSIS
    One-time setup of the DYMO patch on a Windows IoT PC.

.DESCRIPTION
    Run in an elevated PowerShell (Run as Administrator) from this folder:

        powershell -ExecutionPolicy Bypass -File .\install-dymo-patch.ps1

    It copies iot-dymo-patch.ps1 to C:\ProgramData\AMH, registers the scheduled
    task "AMH IoT DYMO patch" (runs as SYSTEM at startup and every hour, so the
    patch comes back after the IoT's weekly self-update), and runs it once now.

    Pass -PaperName if your DYMO labels are not the 30334 size.
#>
param(
    [string]$PaperName = "30334 2-1/4 in x 1-1/4 in"
)

$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this in an elevated PowerShell (Run as Administrator)."
}

$dir = Join-Path $env:ProgramData "AMH"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$target = Join-Path $dir "iot-dymo-patch.ps1"
Copy-Item (Join-Path $PSScriptRoot "iot-dymo-patch.ps1") $target -Force

$taskName = "AMH IoT DYMO patch"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$target`" -PaperName `"$PaperName`""
$atStartup = New-ScheduledTaskTrigger -AtStartup
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)
$runAs = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($atStartup, $hourly) `
    -Principal $runAs -Force | Out-Null
Write-Host "Scheduled task '$taskName' registered."

& $target -PaperName $PaperName
Write-Host "Patch run. Log: $(Join-Path $dir 'iot-dymo-patch.log')"
Get-Content (Join-Path $dir "iot-dymo-patch.log") -Tail 5 -ErrorAction SilentlyContinue
