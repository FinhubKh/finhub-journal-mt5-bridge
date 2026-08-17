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
  throw "Missing $Root\.env - refuse deploy without env"
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
      if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $($_.Name) code=$LASTEXITCODE" }
      Log ("Mirrored dir {0}" -f $_.Name)
    } else {
      Copy-Item $_.FullName $dest -Force
      Log ("Copied file {0}" -f $_.Name)
    }
  }
} else {
  Log "No staging dir - refreshing deps/services only"
}

# 2) Dependencies (venv only)
Log "pip install -r requirements.txt"
& $Pip install -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install requirements failed" }
& $Pip install "MetaTrader5>=5.0.45" "numpy<2" pywin32 psutil
if ($LASTEXITCODE -ne 0) { throw "pip install MT5 extras failed" }

function Stop-ListenersOnPort([int]$Port) {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
      $procId = $_.OwningProcess
      if ($procId) {
        Log ("Stopping pid={0} on port {1}" -f $procId, $Port)
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      }
    }
}

function Restart-NamedTask([string]$Name) {
  $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  if (-not $task) {
    Log ("WARNING: scheduled task $Name missing")
    return $false
  }
  Log "Restarting scheduled task $Name"
  Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
  Start-ScheduledTask -TaskName $Name
  return $true
}

# 3) Restart API via scheduled tasks (SYSTEM) so it survives SSH logoff.
# Start-Process uvicorn from this session dies when deploy SSH ends.
Log "Restarting API scheduled tasks"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*uvicorn*app.main*" } |
  ForEach-Object {
    Log ("Stop uvicorn pid={0}" -f $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Stop-ListenersOnPort 80
Stop-ListenersOnPort 8788
Start-Sleep -Seconds 2

$api80 = Restart-NamedTask "FinhubkhApi80"
$api8788 = Restart-NamedTask "FinhubkhApi8788"
if (-not ($api80 -or $api8788)) {
  Log "No API scheduled tasks - starting uvicorn as last resort"
  Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","80" -WorkingDirectory $Root -WindowStyle Hidden
  Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788" -WorkingDirectory $Root -WindowStyle Hidden
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
if (-not (Restart-NamedTask "FinhubkhMt5Worker")) {
  Log "Starting workers.supervisor via process (no FinhubkhMt5Worker task)"
  Start-Process -FilePath $VenvPy -ArgumentList "-m","workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized
}

# 5) Health
Start-Sleep -Seconds 6
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

if (Test-Path $Staging) {
  Remove-Item $Staging -Recurse -Force -ErrorAction SilentlyContinue
  Log "Removed staging"
}

Log "=== deploy-remote done ==="
