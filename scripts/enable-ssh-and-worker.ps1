$ErrorActionPreference = "Continue"
$Log = "C:\finhubkh\ssh-enable.log"
function Log($m) { Add-Content -Path $Log -Value "$(Get-Date -Format o) $m"; Write-Output $m }
Log "=== ssh enable begin ==="

$ssh = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $ssh) {
  Log "Installing OpenSSH Server..."
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
  Start-Service sshd
  Set-Service -Name sshd -StartupType Automatic
  Log "sshd installed"
} else {
  Start-Service sshd -ErrorAction SilentlyContinue
  Set-Service -Name sshd -StartupType Automatic
  Log "sshd already present, started"
}

New-NetFirewallRule -DisplayName "OpenSSH Server" -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction SilentlyContinue | Out-Null

$User = "finhubkh_admin"
$PassPlain = $env:FINHUBKH_BRIDGE_PASS
if (-not $PassPlain) { throw "Set FINHUBKH_BRIDGE_PASS before running this script" }
net user $User $PassPlain | Out-Null
$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $RegPath -Name "AutoAdminLogon" -Value "1"
Set-ItemProperty -Path $RegPath -Name "DefaultUserName" -Value $User
Set-ItemProperty -Path $RegPath -Name "DefaultPassword" -Value $PassPlain

$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"
$RedisDir = "C:\finhubkh\redis"
$server = Get-ChildItem -Path $RedisDir -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($server -and -not (Get-Process redis-server -ErrorAction SilentlyContinue)) {
  $confPath = Join-Path $server.DirectoryName "redis.finhubkh.conf"
  Start-Process -FilePath $server.FullName -ArgumentList $confPath -WorkingDirectory $server.DirectoryName -WindowStyle Hidden
}
if (Test-Path $VenvPy) {
  if (-not (Get-NetTCPConnection -LocalPort 8788 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788" -WorkingDirectory $Root -WindowStyle Hidden
  }
  if (-not (Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $VenvPy -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","80" -WorkingDirectory $Root -WindowStyle Hidden
  }
}

@'
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"
$Log = "C:\finhubkh\worker-interactive.log"
function Log($m) { Add-Content -Path $Log -Value "$(Get-Date -Format o) $m" }
Start-Sleep -Seconds 5
Log "kickoff"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*workers.supervisor*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Test-Path $VenvPy) {
  Start-Process -FilePath $VenvPy -ArgumentList "-m","workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized
  Log "supervisor started"
}
'@ | Set-Content "C:\finhubkh\start-worker-interactive.ps1" -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\finhubkh\start-worker-interactive.ps1"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
Unregister-ScheduledTask -TaskName "FinhubkhMt5WorkerLogon" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FinhubkhMt5WorkerLogon" -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
try { Start-ScheduledTask -TaskName "FinhubkhMt5WorkerLogon"; Log "Started FinhubkhMt5WorkerLogon task" } catch { Log "Start-ScheduledTask: $($_.Exception.Message)" }

Log "=== ssh enable done ==="
