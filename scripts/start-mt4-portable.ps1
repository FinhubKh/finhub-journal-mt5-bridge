# Launch portable MT4 once (interactive session) so the default profile + EA can load.
$ErrorActionPreference = "Continue"
$Dst = "C:\finhubkh\mt4-portable"
$Exe = Join-Path $Dst "terminal.exe"
$Principal = New-ScheduledTaskPrincipal -UserId finhubkh_admin -LogonType Interactive -RunLevel Highest
$Action = New-ScheduledTaskAction -Execute $Exe -Argument "/portable" -WorkingDirectory $Dst
Unregister-ScheduledTask -TaskName FinhubkhMt4Portable -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName FinhubkhMt4Portable -Action $Action -Principal $Principal -Force | Out-Null
Enable-ScheduledTask -TaskName FinhubkhMt4Portable | Out-Null
Start-ScheduledTask -TaskName FinhubkhMt4Portable
Write-Host "Started FinhubkhMt4Portable"
