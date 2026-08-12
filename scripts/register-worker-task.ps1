# Register a Windows Scheduled Task so the MT5 worker starts at logon.
# Run once (Admin PowerShell) after start-bridge.ps1 has created .venv.
#
# Usage:
#   cd C:\finhubkh-mt5-bridge
#   .\scripts\register-worker-task.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$TaskName = "FinhubkhMt5Worker"

if (-not (Test-Path $Python)) {
    throw "Missing $Python — run .\scripts\start-bridge.ps1 once first."
}

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m workers.supervisor" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null

Write-Host "Registered scheduled task '$TaskName' (runs at logon)."
Write-Host "Start now: Start-ScheduledTask -TaskName $TaskName"
