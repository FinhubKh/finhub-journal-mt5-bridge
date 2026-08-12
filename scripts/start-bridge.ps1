# Start Redis+API (Docker) and the MT5 worker supervisor on Windows.
# Run after bootstrap-windows.ps1, Docker Desktop is running (Linux containers),
# and MetaTrader 5 is installed.
#
# Usage (Admin PowerShell):
#   cd C:\finhubkh-mt5-bridge
#   .\scripts\start-bridge.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path "$Root\.env")) {
    throw "Missing .env"
}

Write-Host "==> Opening Windows Firewall for bridge ports (80, 8788)"
New-NetFirewallRule -DisplayName "Finhubkh MT5 Bridge HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "Finhubkh MT5 Bridge API" -Direction Inbound -Protocol TCP -LocalPort 8788 -Action Allow -ErrorAction SilentlyContinue | Out-Null

Write-Host "==> Building and starting Docker Compose (Redis + API)"
docker compose up -d --build
docker compose ps

Write-Host "==> Installing Python deps for host worker"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) {
    $py = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $py) { throw "Python not found on PATH" }

& $py.Source -m venv .venv
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Root\.venv\Scripts\pip.exe" install -r requirements.txt
& "$Root\.venv\Scripts\pip.exe" install MetaTrader5==5.0.45 pywin32 psutil

Write-Host "==> Health check (local)"
Start-Sleep -Seconds 3
try {
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:8788/health" -UseBasicParsing -TimeoutSec 10
    Write-Host "Health: $($health.StatusCode) $($health.Content)"
} catch {
    Write-Warning "Health check failed — is Docker running? $($_.Exception.Message)"
}

Write-Host "==> Starting worker supervisor in this window (keep open)"
Write-Host "    Or register a scheduled task: .\scripts\register-worker-task.ps1"
& "$Root\.venv\Scripts\python.exe" -m workers.supervisor
