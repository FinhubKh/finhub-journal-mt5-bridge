# Deploy refresh on the Windows bridge VM.
# Called by GitHub Actions after syncing app code (preserves .env / .venv / MT5).
$ErrorActionPreference = "Stop"
$Log = "C:\finhubkh\deploy-remote.log"
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"
$Pip = "$Root\.venv\Scripts\pip.exe"
$Staging = "C:\finhubkh\deploy-staging"

function Log($m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line -ErrorAction SilentlyContinue
  Write-Host $line
}

Log "=== deploy-remote begin ==="

if (-not (Test-Path $Root)) {
  throw "Missing bridge root $Root"
}
if (-not (Test-Path "$Root\.env")) {
  throw "Missing $Root\.env — refuse deploy without env"
}
if (-not (Test-Path $VenvPy)) {
  throw "Missing venv python at $VenvPy"
}

# 1) Apply staged files if present (uploaded by CI)
if (Test-Path $Staging) {
  Log "Syncing staged files into $Root"
  $exclude = @(".env", ".venv", "logs", ".git", "__pycache__", ".pytest_cache")
  Get-ChildItem $Staging -Force | ForEach-Object {
    if ($exclude -contains $_.Name) {
      Log ("Skip {0}" -f $_.Name)
      return
    }
    $dest = Join-Path $Root $_.Name
    if ($_.PSIsContainer) {
      robocopy $_.FullName $dest /MIR /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache logs .venv .git | Out-Null
      # robocopy exit codes 0-7 are success-ish
      if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $($_.Name) code=$LASTEXITCODE" }
      Log ("Mirrored dir {0}" -f $_.Name)
    } else {
      Copy-Item $_.FullName $dest -Force
      Log ("Copied file {0}" -f $_.Name)
    }
  }
} else {
  Log "No staging dir — refreshing deps/services only"
}

# 2) Dependencies (venv only)
Log "pip install -r requirements.txt"
& $Pip install -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install requirements failed" }
# Windows MT5 extras (not installed in Linux CI)
& $Pip install "MetaTrader5>=5.0.45" "numpy<2" pywin32 psutil
if ($LASTEXITCODE -ne 0) { throw "pip install MT5 extras failed" }

# 3) Restart API — prefer Docker Compose (Redis + API); fall back to host uvicorn.
$composeFile = Join-Path $Root "docker-compose.yml"
if ((Test-Path $composeFile) -and (Get-Command docker -ErrorAction SilentlyContinue)) {
  Log "Rebuilding Docker Compose API (preserves Redis volume)"
  Push-Location $Root
  try {
    docker compose up -d --build --force-recreate api
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed code=$LASTEXITCODE" }
  } finally {
    Pop-Location
  }
} else {
  Log "Docker Compose unavailable — restarting host uvicorn tasks"
  foreach ($name in @("FinhubkhApi80", "FinhubkhApi8788")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
      Log "Restarting $name"
      Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
      Start-Sleep -Seconds 1
      Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*uvicorn*app.main*" } |
        ForEach-Object {
          Log ("Stop uvicorn pid={0}" -f $_.ProcessId)
          Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
      Start-ScheduledTask -TaskName $name
    } else {
      Log "WARNING: scheduled task $name missing — starting uvicorn directly"
    }
  }

  Start-Sleep -Seconds 3
  if (-not (Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)) {
    Log "Starting API :80 via venv"
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","80" -WorkingDirectory $Root -WindowStyle Hidden
  }
  if (-not (Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue)) {
    Log "Starting API :8788 via venv"
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788" -WorkingDirectory $Root -WindowStyle Hidden
  }
}

# 4) Restart supervisor/workers only (do NOT kill terminal64)
Log "Restarting workers.supervisor (MT5 stays up)"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -and (
      $_.CommandLine -like "*workers.supervisor*" -or
      $_.CommandLine -like "*workers.run_worker*"
    )
  } |
  ForEach-Object {
    Log ("Stop worker pid={0}" -f $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 2
Start-Process -FilePath $VenvPy -ArgumentList "-m","workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized

# 5) Health
Start-Sleep -Seconds 4
try {
  $h = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1/health -TimeoutSec 10).Content
  Log "health $h"
} catch {
  try {
    $h = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8788/health -TimeoutSec 10).Content
    Log "health8788 $h"
  } catch {
    throw ("Health check failed: {0}" -f $_.Exception.Message)
  }
}

# Cleanup staging
if (Test-Path $Staging) {
  Remove-Item $Staging -Recurse -Force -ErrorAction SilentlyContinue
  Log "Removed staging"
}

Log "=== deploy-remote done ==="
