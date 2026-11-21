<#
.SYNOPSIS
    Make Odoo's Windows IoT print DYMO labels at the right size and orientation.

.DESCRIPTION
    The Windows IoT prints PDFs with its bundled SumatraPDF and passes
    "-print-settings duplex". SumatraPDF then ignores the DYMO's paper size and
    orientation set in Windows printer properties, so labels come out half size
    or sideways even though the PDF from Odoo is correct.

    This script patches the IoT's printer_driver_W.py so that any printer whose
    name contains "DYMO" gets the label paper and landscape instead of duplex.
    Every other printer is untouched.

    The IoT resets its own code when it updates itself (weekly), which wipes the
    patch. Run this script from a scheduled task (see install-dymo-patch.ps1);
    it is safe to run any number of times, and only restarts the IoT service
    when it actually had to re-apply the patch.

.PARAMETER PaperName
    The paper name exactly as the DYMO driver lists it (Printer properties >
    Printing Preferences > Advanced > Paper Size). Default is the 30334 label
    (2-1/4 in x 1-1/4 in).
#>
param(
    [string]$PaperName = "30334 2-1/4 in x 1-1/4 in",
    [string]$NameMatch = "DYMO",
    [string]$ServiceName = "odoo-iot"
)

$ErrorActionPreference = "Stop"
$logDir = Join-Path $env:ProgramData "AMH"
$log = Join-Path $logDir "iot-dymo-patch.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
function Write-Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -Path $log
}

$marker = "# AMH-DYMO-PATCH"
$drivers = Get-ChildItem -Path "$env:ProgramFiles\Odoo IoT*\odoo\addons\iot_drivers\iot_handlers\drivers\printer_driver_W.py" -ErrorAction SilentlyContinue
if (-not $drivers) {
    Write-Log "printer_driver_W.py not found under '$env:ProgramFiles\Odoo IoT*' - nothing to do."
    exit 0
}

# The stock code we replace (same in Odoo 19.0, saas-19.x and master):
#             if duplex:
#                 args.extend(["-print-settings", "duplex"])
$pattern = '(?m)^(?<i>[ \t]+)if duplex:\r?\n[ \t]+args\.extend\(\["-print-settings", "duplex"\]\)'

$changed = $false
foreach ($file in $drivers) {
    $text = [System.IO.File]::ReadAllText($file.FullName)
    if ($text.Contains($marker)) {
        continue
    }
    $m = [regex]::Match($text, $pattern)
    if (-not $m.Success) {
        Write-Log "Expected duplex block not found in $($file.FullName) - Odoo changed the driver; patch not applied."
        continue
    }
    $i = $m.Groups["i"].Value
    $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $settings = "paper=$PaperName,landscape"
    $block = @(
        "$i$marker",
        "$i" + "if `"$($NameMatch.ToUpper())`" in (self.device_name or `"`").upper():",
        "$i    args.extend([`"-print-settings`", `"$settings`"])",
        "$i" + "elif duplex:",
        "$i    args.extend([`"-print-settings`", `"duplex`"])"
    ) -join $nl
    $text = $text.Substring(0, $m.Index) + $block + $text.Substring($m.Index + $m.Length)
    Copy-Item $file.FullName "$($file.FullName).orig" -Force
    [System.IO.File]::WriteAllText($file.FullName, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Log "Patched $($file.FullName) ($NameMatch -> '$settings')."
    $changed = $true
}

if ($changed) {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Restart-Service -Name $ServiceName -Force
        Write-Log "Restarted service $ServiceName."
    } else {
        Write-Log "Service $ServiceName not found - restart the Odoo IoT yourself."
    }
}
