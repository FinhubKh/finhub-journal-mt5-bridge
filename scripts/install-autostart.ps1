# Install reboot-safe autologon + AtLogOn boot for the MT5 bridge.
# Run elevated on the Windows VPS once.
$ErrorActionPreference = "Continue"
$Log = "C:\finhubkh\install-autostart.log"
function Log($m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line
  Write-Host $line
}

$User = "finhubkh_admin"
# Prefer env FINHUBKH_BRIDGE_PASS, else prompt once (do not hardcode secrets in git).
$Pass = $env:FINHUBKH_BRIDGE_PASS
if (-not $Pass) {
  $secure = Read-Host "Password for $User (autologon)" -AsSecureString
  $Pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  )
}
if (-not $Pass) { throw "Password required (set FINHUBKH_BRIDGE_PASS or enter interactively)" }
$BootPs1 = "C:\finhubkh\boot-bridge.ps1"
$Root = "C:\finhubkh\finhubkh-mt5-bridge"
$VenvPy = "$Root\.venv\Scripts\python.exe"

Log "=== install-autostart begin ==="

# Copy boot script into place if deployed beside this installer
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $here "boot-bridge.ps1")) {
  Copy-Item (Join-Path $here "boot-bridge.ps1") $BootPs1 -Force
  Log "Installed boot-bridge.ps1"
}
if (-not (Test-Path $BootPs1)) {
  throw "Missing $BootPs1"
}

# Keep password in sync for autologon
net user $User $Pass | Out-Null
$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $RegPath -Name "AutoAdminLogon" -Value "1"
Set-ItemProperty -Path $RegPath -Name "DefaultUserName" -Value $User
Set-ItemProperty -Path $RegPath -Name "DefaultPassword" -Value $Pass
Set-ItemProperty -Path $RegPath -Name "DefaultDomainName" -Value $env:COMPUTERNAME
Log "Autologon enabled for $User"

# Remove experimental / duplicate tasks
$junk = @(
  "FinhubkhEnumWindows",
  "FinhubkhExnessProbe",
  "FinhubkhExplorerRun",
  "FinhubkhMt5Ui",
  "FinhubkhScreenshot",
  "FinhubkhVbsProbe",
  "FinhubkhMt5ConfigLogin",
  "FinhubkhMt5Portable",
  "FinhubkhMt5Boot",
  "FinhubkhMt5WorkerLogon",
  "FinhubkhMt5SupervisorNow",
  "FinhubkhStartMt5",
  "FinhubkhExnessProbe"
)
foreach ($t in $junk) {
  Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
  Log "Removed task $t"
}

# Redis + API tasks (venv only) — run at startup even before logon is fine for API
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$sysPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Redis
$redis = Get-ChildItem C:\finhubkh\redis -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($redis) {
  $conf = Join-Path $redis.DirectoryName "redis.finhubkh.conf"
  if (-not (Test-Path $conf)) { $conf = Join-Path $redis.DirectoryName "redis.conf" }
  $args = if (Test-Path $conf) { "`"$conf`"" } else { "" }
  $action = New-ScheduledTaskAction -Execute $redis.FullName -Argument $args -WorkingDirectory $redis.DirectoryName
  $trigger = New-ScheduledTaskTrigger -AtStartup
  Unregister-ScheduledTask -TaskName "FinhubkhRedis" -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName "FinhubkhRedis" -Action $action -Trigger $trigger -Principal $sysPrincipal -Settings $settings -Force | Out-Null
  Log "Registered FinhubkhRedis"
}

# API 80 / 8788 — venv only
foreach ($pair in @(
  @{ Name = "FinhubkhApi80"; Port = 80 },
  @{ Name = "FinhubkhApi8788"; Port = 8788 }
)) {
  $action = New-ScheduledTaskAction -Execute $VenvPy -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port $($pair.Port)" -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtStartup
  Unregister-ScheduledTask -TaskName $pair.Name -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName $pair.Name -Action $action -Trigger $trigger -Principal $sysPrincipal -Settings $settings -Force | Out-Null
  Log "Registered $($pair.Name)"
}

# Interactive boot at user logon (MT5 + worker)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BootPs1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
# Delay so desktop is ready
$trigger.Delay = "PT30S"
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
Unregister-ScheduledTask -TaskName "FinhubkhBridgeBoot" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FinhubkhBridgeBoot" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Log "Registered FinhubkhBridgeBoot (AtLogOn +30s)"

# Also a recurring watchdog every 5 minutes while logged on
$wTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
Unregister-ScheduledTask -TaskName "FinhubkhBridgeWatchdog" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FinhubkhBridgeWatchdog" -Action $action -Trigger $wTrigger -Principal $principal -Settings $settings -Force | Out-Null
Log "Registered FinhubkhBridgeWatchdog (every 5m)"

# Kill current Python311 duplicates now
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*Python311*" -and ($_.CommandLine -like "*uvicorn*" -or $_.CommandLine -like "*workers*") } |
  ForEach-Object {
    Log ("Killing Python311 pid={0}" -f $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

# Run boot once now
Log "Running boot-bridge.ps1 once..."
Start-ScheduledTask -TaskName "FinhubkhBridgeBoot"
Start-Sleep -Seconds 20

Log "=== tasks ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like "Finhubkh*" } |
  ForEach-Object { Log ("TASK {0} state={1}" -f $_.TaskName, $_.State) }

Log "=== processes ==="
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -eq "terminal64.exe" -or
    ($_.CommandLine -and ($_.CommandLine -like "*workers*" -or $_.CommandLine -like "*uvicorn*"))
  } |
  ForEach-Object { Log ("{0} {1}" -f $_.ProcessId, $_.CommandLine) }

try {
  $h = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1/health).Content
  Log "health $h"
} catch {
  Log ("health error: {0}" -f $_.Exception.Message)
}

Log "=== install-autostart done ==="
