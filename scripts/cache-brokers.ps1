# Download + silent-install branded MT5 terminals for catalog brokers.
# Branded builds ship with that broker's server list, which avoids IPC timeout
# on first investor verify for that brand.
#
# Usage (Admin PowerShell on the bridge VM):
#   cd C:\finhubkh\finhubkh-mt5-bridge
#   .\scripts\cache-brokers.ps1
#   .\scripts\cache-brokers.ps1 -ProbeOnly
#   .\scripts\cache-brokers.ps1 -Brokers exness,xm,pepperstone

param(
  [string[]]$Brokers = @(),
  [switch]$ProbeOnly,
  [switch]$SkipProbe
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BrokersRoot = "C:\finhubkh\mt5-brokers"
$DownloadDir = "C:\finhubkh\mt5-installers"
$Portable = "C:\finhubkh\mt5-portable"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$Log = "C:\finhubkh\cache-brokers.log"
$MapPath = Join-Path $Root "config\mt5_terminal_map.json"

function Log([string]$m) {
  $line = "{0} {1}" -f (Get-Date -Format o), $m
  Add-Content -Path $Log -Value $line
  Write-Host $line
}

# Official MetaQuotes CDN installers (GET works; HEAD often 403).
$Catalog = [ordered]@{
  lirunex     = @{ Url = "https://download.mql5.com/cdn/web/lirunex.limited/mt5/lirunexlimited5setup.exe";     Prefixes = @("Lirunex") }
  exness      = @{ Url = "https://download.mql5.com/cdn/web/exness.technologies.ltd/mt5/exness5setup.exe";     Prefixes = @("Exness-") }
  xm          = @{ Url = "https://download.mql5.com/cdn/web/xm.global.limited/mt5/xmglobal5setup.exe";         Prefixes = @("XMGlobal", "XM-") }
  pepperstone = @{ Url = "https://download.mql5.com/cdn/web/pepperstone.group.limited/mt5/pepperstone5setup.exe"; Prefixes = @("Pepperstone") }
  fbs         = @{ Url = "https://download.mql5.com/cdn/web/fbs.markets.inc/mt5/fbs5setup.exe";               Prefixes = @("FBS-") }
  roboforex   = @{ Url = "https://download.mql5.com/cdn/web/robomarkets.ltd/mt5/robomarkets5setup.exe";       Prefixes = @("RoboForex") }
  tickmill    = @{ Url = "https://download.mql5.com/cdn/web/tickmill.uk.ltd/mt5/tickmill5setup.exe";          Prefixes = @("Tickmill") }
  alpari      = @{ Url = "https://download.mql5.com/cdn/web/alpari/mt5/alpari5setup.exe";                     Prefixes = @("Alpari") }
  forextime   = @{ Url = "https://download.mql5.com/cdn/web/forextime.ltd/mt5/forextime5setup.exe";           Prefixes = @("ForexTime") }
}

New-Item -ItemType Directory -Force -Path $BrokersRoot, $DownloadDir | Out-Null
Log "=== cache-brokers begin ProbeOnly=$ProbeOnly ==="

if ($Brokers.Count -gt 0) {
  $selected = @()
  foreach ($b in $Brokers) {
    $selected += @($b -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  }
} else {
  $selected = @($Catalog.Keys)
}

if (-not $ProbeOnly) {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  foreach ($id in $selected) {
    if (-not $Catalog.Contains($id)) {
      Log "SKIP unknown broker id=$id"
      continue
    }
    $meta = $Catalog[$id]
    $dest = Join-Path $BrokersRoot $id
    $term = Join-Path $dest "terminal64.exe"
    if (Test-Path $term) {
      Log "EXISTS $id -> $term"
      continue
    }

    $setup = Join-Path $DownloadDir ("{0}5setup.exe" -f $id)
    try {
      Log "DOWNLOAD $id"
      Invoke-WebRequest -Uri $meta.Url -OutFile $setup -TimeoutSec 300 -UserAgent "Mozilla/5.0"
      Log ("DOWNLOADED {0} size={1}" -f $id, (Get-Item $setup).Length)
    } catch {
      Log ("FAIL download {0}: {1}" -f $id, $_.Exception.Message)
      continue
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Log "INSTALL $id -> $dest"
    $p = Start-Process -FilePath $setup -ArgumentList "/auto", "/path:$dest" -PassThru
    $timedOut = -not $p.WaitForExit(180000)  # 3 minutes max per installer
    if ($timedOut) {
      Log "TIMEOUT installer id=$id - killing"
      try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
      Get-Process | Where-Object { $_.ProcessName -match 'setup|LiveUpdate|metaeditor' } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    } else {
      Log ("INSTALLER exit={0} id={1}" -f $p.ExitCode, $id)
    }

    if (-not (Test-Path $term)) {
      $found = Get-ChildItem -Path $dest -Filter terminal64.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found) {
        Log ("FOUND nested terminal {0}" -f $found.FullName)
      } else {
        Log "WARN no terminal64.exe for $id"
      }
    } else {
      Log "OK installed $id"
    }

    # First launch warms company/server cache for that brand.
    if (Test-Path $term) {
      try {
        Start-Process -FilePath $term -WorkingDirectory (Split-Path $term) -WindowStyle Minimized
        Start-Sleep -Seconds 12
        Get-Process -Name terminal64 -ErrorAction SilentlyContinue |
          Where-Object { $_.Path -and $_.Path -like ("*{0}*" -f $id) } |
          Stop-Process -Force -ErrorAction SilentlyContinue
      } catch {
        Log ("WARN warm-start {0}: {1}" -f $id, $_.Exception.Message)
      }
    }
  }

  # Prefer Lirunex portable bases already present; keep portable as default.
  # Rewrite terminal map from whatever is actually installed.
  $prefixes = [ordered]@{}
  foreach ($id in $Catalog.Keys) {
    $term = Join-Path $BrokersRoot "$id\terminal64.exe"
    if (-not (Test-Path $term)) { continue }
    foreach ($prefix in $Catalog[$id].Prefixes) {
      $prefixes[$prefix] = $term
    }
  }
  # Keep Lirunex path alias if older install folder exists
  $legacyLir = "C:\finhubkh\mt5-lirunex\terminal64.exe"
  if ((Test-Path $legacyLir) -and -not $prefixes.Contains("Lirunex")) {
    $prefixes["Lirunex"] = $legacyLir
  }

  $map = [ordered]@{
    _comment = "Generated by scripts/cache-brokers.ps1"
    default  = ($Portable + "\terminal64.exe")
    prefixes = $prefixes
  }
  $json = $map | ConvertTo-Json -Depth 5
  New-Item -ItemType Directory -Force -Path (Split-Path $MapPath) | Out-Null
  Set-Content -Path $MapPath -Value $json -Encoding UTF8
  Log "Wrote $MapPath"

  # Optional: merge a community servers.dat into portable (backup first).
  $publicDat = Join-Path $DownloadDir "servers-public.dat"
  try {
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/hudsonventura/MT5_Docker/main/servers.dat" `
      -OutFile $publicDat -TimeoutSec 120 -UserAgent "Mozilla/5.0"
    $portableDat = Join-Path $Portable "Config\servers.dat"
    if (Test-Path $portableDat) {
      Copy-Item $portableDat ($portableDat + ".bak-before-public") -Force
    }
    if ((Get-Item $publicDat).Length -gt 100KB) {
      # Keep existing if larger; otherwise adopt public catalog.
      $usePublic = (-not (Test-Path $portableDat)) -or ((Get-Item $publicDat).Length -gt (Get-Item $portableDat).Length)
      if ($usePublic) {
        Copy-Item $publicDat $portableDat -Force
        Log "Installed public servers.dat into portable"
      } else {
        Log "Kept existing portable servers.dat (larger/equal)"
      }
    }
  } catch {
    Log ("WARN public servers.dat: {0}" -f $_.Exception.Message)
  }
}

if (-not $SkipProbe -and (Test-Path $VenvPy)) {
  Log "=== probe catalog servers (auth fail = cached, IPC timeout = missing) ==="
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like "*workers.supervisor*" -or $_.CommandLine -like "*workers.run_worker*") } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      Log ("Stopped worker pid={0}" -f $_.ProcessId)
    }
  Start-Sleep 2

  $probeScript = Join-Path $Root "scripts\probe_broker_cache.py"
  & $VenvPy $probeScript $MapPath "$Portable\terminal64.exe" 2>&1 | ForEach-Object { Log $_ }

  Start-Process -FilePath $VenvPy -ArgumentList "-m", "workers.supervisor" -WorkingDirectory $Root -WindowStyle Minimized
  Log "Supervisor restarted"
}

Log "=== cache-brokers done ==="
Log "Missing CDN (need GUI search or demo login once): IC Markets, FxPro, Fusion Markets, LiteFinance, ST Markets"
