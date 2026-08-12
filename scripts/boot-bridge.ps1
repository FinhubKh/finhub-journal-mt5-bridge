# Durable interactive boot for Finhubkh MT5 bridge.
# Must run in the logged-on desktop session (AtLogOn), not as SYSTEM.
$ErrorActionPreference = "Continue"
$Log = "C:\finhubkh\boot-bridge.log"
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"
$Mt5 = "C:\finhubkh\mt5-portable\terminal64.exe"
$Mt5Args = "/portable /config:C:\finhubkh\mt5-portable\Config\login.ini"
$RedisDir = "C:\finhubkh\redis"

function Log($m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line -ErrorAction SilentlyContinue
}

function Ensure-Port($port, $moduleArgs) {
  $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($listening) { return }
  Log "Starting API on port $port"
  Start-Process -FilePath $VenvPy -ArgumentList $moduleArgs -WorkingDirectory $Root -WindowStyle Hidden
}

function Stop-CmdMatches($pattern) {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like $pattern } |
    ForEach-Object {
      Log ("Stop pid={0} {1}" -f $_.ProcessId, $_.CommandLine)
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Log "=== boot-bridge begin ==="

# 1) Redis
$redisProc = Get-Process redis-server -ErrorAction SilentlyContinue
if (-not $redisProc) {
  $server = Get-ChildItem -Path $RedisDir -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($server) {
    $conf = Join-Path $server.DirectoryName "redis.finhubkh.conf"
    if (-not (Test-Path $conf)) { $conf = Join-Path $server.DirectoryName "redis.conf" }
    Log "Starting Redis $($server.FullName)"
    if (Test-Path $conf) {
      Start-Process -FilePath $server.FullName -ArgumentList $conf -WorkingDirectory $server.DirectoryName -WindowStyle Hidden
    } else {
      Start-Process -FilePath $server.FullName -WorkingDirectory $server.DirectoryName -WindowStyle Hidden
    }
    Start-Sleep -Seconds 2
  } else {
    Log "WARNING: redis-server.exe not found"
  }
}

# 2) API (venv only). Do not kill Python311 children of the venv launcher.
if (Test-Path $VenvPy) {
  Ensure-Port 8788 @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8788")
  Ensure-Port 80 @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80")
} else {
  Log "ERROR: missing $VenvPy"
}

# 3) MetaTrader 5 portable (interactive GUI required for IPC)
$mt5Running = Get-Process terminal64 -ErrorAction SilentlyContinue
if (-not $mt5Running) {
  if (Test-Path $Mt5) {
    Log "Starting MT5 portable"
    Start-Process -FilePath $Mt5 -ArgumentList $Mt5Args -WorkingDirectory (Split-Path $Mt5)
  } else {
    Log "ERROR: missing $Mt5"
  }
} else {
  Log ("MT5 already running pid={0}" -f ($mt5Running | Select-Object -First 1).Id)
}

# Wait for MT5 named pipe (IPC ready)
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 2
  try {
    $pipes = [System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -match "MT5\.Terminal" }
    if ($pipes -and (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
      $ready = $true
      Log ("MT5 IPC pipe ready after {0}s" -f ($i * 2))
      break
    }
  } catch { }
}
if (-not $ready) { Log "WARNING: MT5 IPC pipe not detected yet - starting worker anyway" }

# Extra settle time after pipe appears (broker login / quotes)
Start-Sleep -Seconds 8

# 4) Exactly one supervisor.
# On Windows, venv python.exe often re-execs into Python311, so WMI shows TWO
# processes per logical process. Count only .venv\Scripts\python.exe entries.
$allSup = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*workers.supervisor*" })
$allWorkers = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*workers.run_worker*" })
$venvSup = @($allSup | Where-Object { $_.CommandLine -like "*\.venv\Scripts\python.exe*" })
$venvWorkers = @($allWorkers | Where-Object { $_.CommandLine -like "*\.venv\Scripts\python.exe*" })

if ($venvSup.Count -gt 1 -or $venvWorkers.Count -gt 2) {
  Log ("Cleaning duplicate workers (venvSup={0} venvWorkers={1})" -f $venvSup.Count, $venvWorkers.Count)
  Stop-CmdMatches "*workers.supervisor*"
  Stop-CmdMatches "*workers.run_worker*"
  Start-Sleep -Seconds 2
  $venvSup = @()
}

if ($venvSup.Count -eq 0) {
  Log "Starting workers.supervisor"
  Start-Process -FilePath $VenvPy -ArgumentList "-m", "workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized
} else {
  Log ("supervisor already running pid={0}" -f $venvSup[0].ProcessId)
}

# Clear stale lock note
try {
  $cli = Get-ChildItem C:\finhubkh\redis -Recurse -Filter redis-cli.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cli) {
    $held = & $cli.FullName GET finhubkh:mt5:terminal_lock
    if ($held) {
      Log "Note: mt5 lock currently set ($held)"
    }
  }
} catch { }

Log "=== boot-bridge done ==="
