# Smoke-test companion EA file IPC (does not need a successful broker login).
$ErrorActionPreference = "Stop"
$Files = "C:\finhubkh\mt4-portable\MQL4\Files"
New-Item -ItemType Directory -Force -Path $Files | Out-Null
Remove-Item "$Files\finhub_bridge_request.json" -ErrorAction SilentlyContinue
Remove-Item "$Files\finhub_bridge_response.json" -ErrorAction SilentlyContinue

$req = '{"action":"verify","login":0,"server":"","request_id":"smoke-1"}'
Set-Content -Path "$Files\finhub_bridge_request.json" -Value $req -Encoding ASCII
Write-Host "Wrote request; waiting up to 45s for EA response..."

$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
  if (Test-Path "$Files\finhub_bridge_response.json") {
    Write-Host "RESPONSE:"
    Get-Content "$Files\finhub_bridge_response.json" -Raw
    exit 0
  }
  Start-Sleep -Seconds 1
}
Write-Host "NO_RESPONSE"
Get-Process terminal -ErrorAction SilentlyContinue | Format-Table Id,ProcessName,StartTime -AutoSize
exit 1
