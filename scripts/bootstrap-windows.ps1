# FinHubKh MT5 Bridge — Windows Server bootstrap (run in elevated PowerShell)
# Prerequisites before this script: RDP in, copy the finhubkh-mt5-bridge folder
# (including .env) to C:\finhubkh-mt5-bridge
#
# Usage (Admin PowerShell):
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   cd C:\finhubkh-mt5-bridge
#   .\scripts\bootstrap-windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> Bridge root: $Root"

if (-not (Test-Path "$Root\.env")) {
    throw "Missing .env — copy it from your local machine before running this script."
}

function Ensure-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget not found. Install App Installer from Microsoft Store, then re-run."
    }
}

Ensure-Winget

Write-Host "==> Installing Python 3.11 (if needed)"
winget install --id Python.Python.3.11 -e --accept-package-agreements --accept-source-agreements --silent

Write-Host "==> Installing Docker Desktop (if needed)"
winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements --silent

Write-Host @"

Manual step required: MetaTrader 5
  1. Download MT5 from your broker (or https://www.metatrader5.com/en/download)
  2. Install to the default path (or note terminal64.exe path)
  3. Update MT5_TERMINAL_PATH in .env if not:
     C:\\Program Files\\MetaTrader 5\\terminal64.exe
  4. Open MT5 once and complete any first-run prompts

After Docker Desktop finishes installing, REBOOT if prompted, then start Docker Desktop
(Linux containers mode) and continue with:
  cd C:\finhubkh-mt5-bridge
  .\scripts\start-bridge.ps1

"@
