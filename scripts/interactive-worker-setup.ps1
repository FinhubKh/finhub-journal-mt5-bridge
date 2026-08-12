# Set known password, autologon, start Redis/API, schedule interactive worker at logon
$ErrorActionPreference = "Continue"
$Log = "C:\finhubkh\setup-interactive.log"
New-Item -ItemType Directory -Force -Path C:\finhubkh | Out-Null
function Log($m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line
  Write-Output $line
}
Log "=== interactive setup begin ==="

$User = "finhubkh_admin"
$PassPlain = $env:FINHUBKH_BRIDGE_PASS
if (-not $PassPlain) { throw "Set FINHUBKH_BRIDGE_PASS before running this script" }
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"

# 1) Reset local password to known value (no special escape issues)
try {
  net user $User $PassPlain | Out-Null
  Log "Password reset for $User"
} catch {
  Log "net user failed: $($_.Exception.Message)"
}

# 2) Autologon
$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $RegPath -Name "AutoAdminLogon" -Value "1"
Set-ItemProperty -Path $RegPath -Name "DefaultUserName" -Value $User
Set-ItemProperty -Path $RegPath -Name "DefaultPassword" -Value $PassPlain
Set-ItemProperty -Path $RegPath -Name "DefaultDomainName" -Value $env:COMPUTERNAME
Log "Autologon registry set"

# Disable lock screen / screen saver interference
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Force | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Name "NoLockScreen" -Value 1 -Type DWord -ErrorAction SilentlyContinue

# 3) Redis + API
$RedisDir = "C:\finhubkh\redis"
$server = Get-ChildItem -Path $RedisDir -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($server -and -not (Get-Process redis-server -ErrorAction SilentlyContinue)) {
  $confPath = Join-Path $server.DirectoryName "redis.finhubkh.conf"
  if (-not (Test-Path $confPath)) {
    @"
bind 127.0.0.1
port 6379
protected-mode yes
appendonly yes
dir $($server.DirectoryName)
"@ | Set-Content -Path $confPath -Encoding ASCII
  }
  Start-Process -FilePath $server.FullName -ArgumentList $confPath -WorkingDirectory $server.DirectoryName -WindowStyle Hidden
  Start-Sleep -Seconds 2
  Log "Redis started"
}
if (Test-Path $VenvPy) {
  if (-not (Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788" -WorkingDirectory $Root -WindowStyle Hidden
    Log "API 8788 started"
  }
  if (-not (Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","80" -WorkingDirectory $Root -WindowStyle Hidden
    Log "API 80 started"
  }
}

# 4) Logon worker script
$workerScript = @'
$ErrorActionPreference = "Continue"
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"
$Log = "C:\finhubkh\worker-interactive.log"
function Log($m) { Add-Content -Path $Log -Value "$(Get-Date -Format o) $m" }
Start-Sleep -Seconds 25
Log "interactive logon worker kickoff"
# Redis
$RedisDir = "C:\finhubkh\redis"
$server = Get-ChildItem -Path $RedisDir -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($server -and -not (Get-Process redis-server -ErrorAction SilentlyContinue)) {
  $confPath = Join-Path $server.DirectoryName "redis.finhubkh.conf"
  Start-Process -FilePath $server.FullName -ArgumentList $confPath -WorkingDirectory $server.DirectoryName -WindowStyle Hidden
  Start-Sleep -Seconds 2
}
# Kill old workers
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -like "*workers.supervisor*" -or $_.CommandLine -like "*uvicorn*app.main*") } |
  ForEach-Object {
    if ($_.CommandLine -like "*workers.supervisor*") {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
# Ensure APIs
if (Test-Path $VenvPy) {
  if (-not (Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788" -WorkingDirectory $Root -WindowStyle Hidden
  }
  if (-not (Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","80" -WorkingDirectory $Root -WindowStyle Hidden
  }
  Log "starting supervisor"
  Start-Process -FilePath $VenvPy -ArgumentList "-m","workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized
  Log "supervisor launched"
} else {
  Log "MISSING venv python"
}
'@
Set-Content -Path "C:\finhubkh\start-worker-interactive.ps1" -Value $workerScript -Encoding ASCII

# Startup folder (runs at interactive logon)
$startupDir = "C:\Users\$User\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
$cmd = "@echo off`r`npowershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\finhubkh\start-worker-interactive.ps1`r`n"
Set-Content -Path "$startupDir\FinhubkhWorker.cmd" -Value $cmd -Encoding ASCII
Log "Startup folder shortcut written"

# Scheduled task at logon (backup)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\finhubkh\start-worker-interactive.ps1"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
Unregister-ScheduledTask -TaskName "FinhubkhMt5WorkerLogon" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FinhubkhMt5WorkerLogon" -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Log "Scheduled task registered"

Unregister-ScheduledTask -TaskName "FinhubkhMt5Worker" -Confirm:$false -ErrorAction SilentlyContinue

# Marker + reboot into autologon
if (-not (Test-Path "C:\finhubkh\INTERACTIVE_READY.flag")) {
  Set-Content -Path "C:\finhubkh\INTERACTIVE_READY.flag" -Value (Get-Date -Format o)
  Log "Rebooting for autologon"
  shutdown /r /t 10 /f /c "Finhubkh interactive MT5 worker"
} else {
  Log "Already prepared; no reboot from marker"
}
Log "=== interactive setup done ==="
