# Clone portable MetaTrader installs into numbered slots for parallel investor sync.
# Run on the Windows bridge VM from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\clone-terminal-slots.ps1
#
# After cloning, attach FinhubJournal_BridgeExport.mq4 to a chart on each MT4 slot,
# set WORKER_POOL_SIZE to match installed slots (e.g. 4-6), and redeploy/restart workers.

param(
    [string]$Mt5Source = "C:\finhubkh\mt5-portable",
    [string]$Mt4Source = "C:\finhubkh\mt4-portable",
    [int]$Mt5Slots = 4,
    [int]$Mt4Slots = 2
)

$ErrorActionPreference = "Stop"

function Copy-PortableSlot {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Warning "Skip: source missing $Source"
        return
    }
    if (Test-Path -LiteralPath $Dest) {
        Write-Host "Exists: $Dest"
        return
    }
    Write-Host "Cloning $Source -> $Dest"
    New-Item -ItemType Directory -Path (Split-Path -Parent $Dest) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Dest -Recurse -Force
}

for ($i = 1; $i -le $Mt5Slots; $i++) {
    Copy-PortableSlot -Source $Mt5Source -Dest "C:\finhubkh\mt5-slot-$i"
}
for ($i = 1; $i -le $Mt4Slots; $i++) {
    Copy-PortableSlot -Source $Mt4Source -Dest "C:\finhubkh\mt4-slot-$i"
}

Write-Host ""
Write-Host "Done. Confirm slots listed in config\mt5_terminal_map.json and config\mt4_terminal_map.json."
Write-Host "Set WORKER_POOL_SIZE to at least the number of slots you want concurrent (e.g. 6)."
