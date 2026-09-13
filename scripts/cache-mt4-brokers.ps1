# Download branded MT4 installers, install under C:\finhubkh\mt4-brokers\<id>,
# then copy *.srv (+ servers.*) into the portable bridge terminal config.
#
# No broker login required — branded builds ship with that broker's server list.
#
# Usage (on the bridge VM):
#   cd C:\finhubkh\finhubkh-mt5-bridge
#   .\scripts\cache-mt4-brokers.ps1
#   .\scripts\cache-mt4-brokers.ps1 -Brokers exness,xm,pepperstone

param(
  [string[]]$Brokers = @(),
  [switch]$SkipInstall,
  [switch]$HarvestOnly
)

$ErrorActionPreference = "Continue"
$BrokersRoot = "C:\finhubkh\mt4-brokers"
$DownloadDir = "C:\finhubkh\mt4-installers"
$PortableConfig = "C:\finhubkh\mt4-portable\config"
$Log = "C:\finhubkh\cache-mt4-brokers.log"

function Log([string]$m) {
  $line = "{0} {1}" -f (Get-Date -Format o), $m
  Add-Content -Path $Log -Value $line
  Write-Host $line
}

# Official MetaQuotes CDN MT4 installers (GET works; HEAD often 403).
# Verified 206 range responses before shipping this catalog.
$Catalog = [ordered]@{
  exness      = @{ Url = "https://download.mql5.com/cdn/web/exness.technologies.ltd/mt4/exness4setup.exe" }
  xm          = @{ Url = "https://download.mql5.com/cdn/web/xm.global.limited/mt4/xmglobal4setup.exe" }
  pepperstone = @{ Url = "https://download.mql5.com/cdn/web/pepperstone.group.limited/mt4/pepperstone4setup.exe" }
  fbs         = @{ Url = "https://download.mql5.com/cdn/web/fbs.markets.inc/mt4/fbs4setup.exe" }
  roboforex   = @{ Url = "https://download.mql5.com/cdn/web/robomarkets.ltd/mt4/robomarkets4setup.exe" }
  alpari      = @{ Url = "https://download.mql5.com/cdn/web/alpari/mt4/alpari4setup.exe" }
  forextime   = @{ Url = "https://download.mql5.com/cdn/web/forextime.ltd/mt4/forextime4setup.exe" }
  blackwell   = @{ Url = "https://download.mql5.com/cdn/web/blackwell.global.investments/mt4/blackwellglobal14setup.exe" }
  metaquotes  = @{ Url = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt4/mt4setup.exe" }
}

function Copy-SrvTree([string]$fromRoot, [string]$label) {
  if (-not (Test-Path $fromRoot)) { return 0 }
  $n = 0
  $files = Get-ChildItem $fromRoot -Recurse -Include "*.srv","servers.dat","servers.ini" -ErrorAction SilentlyContinue
# Prefer newer / larger srv for same name; never copy a file onto itself.
    foreach ($f in @($files)) {
      if ($f.DirectoryName -eq $PortableConfig) { continue }
      $dest = Join-Path $PortableConfig $f.Name
      if ((Test-Path $dest) -and ((Resolve-Path $f.FullName).Path -eq (Resolve-Path $dest).Path)) {
        continue
      }
      if ((Test-Path $dest) -and $f.Extension -eq ".srv") {
        $existing = Get-Item $dest
        if ($existing.Length -ge $f.Length -and $existing.LastWriteTime -ge $f.LastWriteTime) {
          continue
        }
      }
      Copy-Item $f.FullName $dest -Force
      $n++
      Log ("COPY [{0}] {1} -> {2}" -f $label, $f.FullName, $dest)
    }
  return $n
}

New-Item -ItemType Directory -Force -Path $BrokersRoot, $DownloadDir, $PortableConfig | Out-Null
Log "=== cache-mt4-brokers begin HarvestOnly=$HarvestOnly SkipInstall=$SkipInstall ==="

if ($Brokers.Count -gt 0) {
  $selected = @()
  foreach ($b in $Brokers) {
    $selected += @($b -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  }
} else {
  $selected = @($Catalog.Keys)
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $HarvestOnly) {
  foreach ($id in $selected) {
    if (-not $Catalog.Contains($id)) {
      Log "SKIP unknown broker id=$id"
      continue
    }
    $meta = $Catalog[$id]
    $dest = Join-Path $BrokersRoot $id
    $term = Join-Path $dest "terminal.exe"
    if ((Test-Path $term) -or $SkipInstall) {
      if (Test-Path $term) { Log "EXISTS $id -> $term" }
      continue
    }

    $setup = Join-Path $DownloadDir ("{0}4setup.exe" -f $id)
    # Reuse already-downloaded blackwell installer if present
    if ($id -eq "blackwell" -and -not (Test-Path $setup)) {
      $legacy = "C:\finhubkh\blackwellglobal14setup.exe"
      if (Test-Path $legacy) {
        Copy-Item $legacy $setup -Force
        Log "REUSED legacy blackwell installer"
      }
    }

    if (-not (Test-Path $setup)) {
      try {
        Log "DOWNLOAD $id"
        Invoke-WebRequest -Uri $meta.Url -OutFile $setup -TimeoutSec 300 -UserAgent "Mozilla/5.0"
        Log ("DOWNLOADED {0} size={1}" -f $id, (Get-Item $setup).Length)
      } catch {
        Log ("FAIL download {0}: {1}" -f $id, $_.Exception.Message)
        continue
      }
    } else {
      Log ("SETUP_PRESENT {0}" -f $setup)
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Log "INSTALL $id -> $dest"
    $p = Start-Process -FilePath $setup -ArgumentList "/auto", "/path:$dest" -PassThru
    $timedOut = -not $p.WaitForExit(180000)
    if ($timedOut) {
      Log "TIMEOUT installer id=$id - killing"
      try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
      Get-Process | Where-Object { $_.ProcessName -match 'setup|LiveUpdate|metaeditor|terminal' } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    } else {
      Log ("INSTALLER exit={0} id={1}" -f $p.ExitCode, $id)
    }

    if (-not (Test-Path $term)) {
      $found = Get-ChildItem -Path $dest -Filter "terminal.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found) {
        Log ("FOUND nested terminal {0}" -f $found.FullName)
      } else {
        # Some installers ignore /path and go to Program Files — harvest later
        Log "WARN no terminal.exe under $dest (will harvest Program Files)"
      }
    } else {
      Log "OK installed $id"
    }

    # Brief warm start so installer may finalize config/*.srv
    $warm = $term
    if (-not (Test-Path $warm)) {
      $nested = Get-ChildItem -Path $dest -Filter "terminal.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($nested) { $warm = $nested.FullName }
    }
    if (Test-Path $warm) {
      try {
        $wp = Start-Process -FilePath $warm -ArgumentList "/portable" -WorkingDirectory (Split-Path $warm) -WindowStyle Minimized -PassThru
        Start-Sleep -Seconds 10
        if ($wp -and -not $wp.HasExited) { Stop-Process -Id $wp.Id -Force -ErrorAction SilentlyContinue }
        Get-Process -Name terminal -ErrorAction SilentlyContinue |
          Where-Object { $_.Path -and $_.Path -like ("*{0}*" -f $id) } |
          Stop-Process -Force -ErrorAction SilentlyContinue
      } catch {
        Log ("WARN warm-start {0}: {1}" -f $id, $_.Exception.Message)
      }
    }
  }
}

# Harvest .srv from branded installs + common Program Files paths into portable
Log "=== harvest .srv into portable config ==="
$before = @(Get-ChildItem $PortableConfig -Filter "*.srv" -ErrorAction SilentlyContinue).Count
$harvestRoots = @(
  $BrokersRoot,
  "C:\Program Files (x86)",
  "C:\Program Files",
  "C:\finhubkh\mt4-blackwell",
  "C:\finhubkh\mt4-portable"
)
foreach ($root in $harvestRoots) {
  if (-not (Test-Path $root)) { continue }
  # Only MetaTrader / broker trader trees under Program Files to avoid scanning everything forever
  if ($root -match "Program Files") {
    Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "MetaTrader|Trader|Exness|XM|Pepperstone|FBS|Robo|Alpari|ForexTime|Blackwell|Tickmill" } |
      ForEach-Object {
        [void](Copy-SrvTree $_.FullName $_.Name)
      }
  } else {
    [void](Copy-SrvTree $root $root)
  }
}
$after = @(Get-ChildItem $PortableConfig -Filter "*.srv" -ErrorAction SilentlyContinue).Count
Log ("portable_srv_before={0} after={1}" -f $before, $after)
Get-ChildItem $PortableConfig -Filter "*.srv" -ErrorAction SilentlyContinue |
  Sort-Object Name |
  ForEach-Object { Log ("SRV {0} ({1} bytes)" -f $_.Name, $_.Length) }

# Write MT4 terminal map (same idea as MT5): route server prefix → branded terminal.
# Modern MT4 keeps servers inside encrypted servers.ini, so one portable cannot
# hold every broker — workers pick the matching branded install instead.
$MapPath = "C:\finhubkh\finhubkh-mt5-bridge\config\mt4_terminal_map.json"
$PrefixCatalog = [ordered]@{
  exness      = @("Exness-", "Exness")
  xm          = @("XMGlobal", "XM-")
  pepperstone = @("Pepperstone")
  fbs         = @("FBS-")
  roboforex   = @("RoboForex", "RoboMarkets")
  alpari      = @("Alpari")
  forextime   = @("ForexTime", "FXTM")
  blackwell   = @("BlackwellGlobal", "Blackwellglobal", "Blackwell")
  metaquotes  = @("MetaQuotes")
}
$prefixes = [ordered]@{}
$eaSrc = "C:\finhubkh\mt4-portable\MQL4\Indicators\FinhubJournal_BridgeExport.ex4"
$eaSrcMq4 = "C:\finhubkh\mt4-portable\MQL4\Indicators\FinhubJournal_BridgeExport.mq4"
$profileSrc = "C:\finhubkh\mt4-portable\profiles"

foreach ($id in $PrefixCatalog.Keys) {
  $term = Join-Path $BrokersRoot "$id\terminal.exe"
  if (-not (Test-Path $term)) { continue }
  $root = Join-Path $BrokersRoot $id
  # Force portable data folder next to terminal.exe (Files IPC path).
  Set-Content -Path (Join-Path $root "portable.ini") -Value "1" -Encoding ASCII
  $indDir = Join-Path $root "MQL4\Indicators"
  New-Item -ItemType Directory -Force -Path $indDir,(Join-Path $root "MQL4\Files") | Out-Null
  if (Test-Path $eaSrc) { Copy-Item $eaSrc $indDir -Force }
  if (Test-Path $eaSrcMq4) { Copy-Item $eaSrcMq4 $indDir -Force }
  if (Test-Path $profileSrc) {
    $profDest = Join-Path $root "profiles"
    New-Item -ItemType Directory -Force -Path $profDest | Out-Null
    Copy-Item (Join-Path $profileSrc "*") $profDest -Recurse -Force -ErrorAction SilentlyContinue
  }
  foreach ($prefix in $PrefixCatalog[$id]) {
    $prefixes[$prefix] = $term
  }
  Log ("MAPPED $id -> $term")
}

$map = [ordered]@{
  _comment = "Generated by scripts/cache-mt4-brokers.ps1 - longest prefix wins"
  default  = "C:\finhubkh\mt4-portable\terminal.exe"
  prefixes = $prefixes
}
New-Item -ItemType Directory -Force -Path (Split-Path $MapPath) | Out-Null
($map | ConvertTo-Json -Depth 5) | Set-Content -Path $MapPath -Encoding UTF8
Log "Wrote $MapPath"

# Point bridge .env at the map if missing
$envPath = "C:\finhubkh\finhubkh-mt5-bridge\.env"
if (Test-Path $envPath) {
  $envText = Get-Content $envPath -Raw
  if ($envText -notmatch "MT4_TERMINAL_MAP_PATH=") {
    Add-Content $envPath "`r`nMT4_TERMINAL_MAP_PATH=C:\\finhubkh\\finhubkh-mt5-bridge\\config\\mt4_terminal_map.json"
    Log "Appended MT4_TERMINAL_MAP_PATH to .env"
  } elseif ($envText -match "MT4_TERMINAL_MAP_PATH=") {
    $envText = $envText -replace "MT4_TERMINAL_MAP_PATH=.*", "MT4_TERMINAL_MAP_PATH=C:\\finhubkh\\finhubkh-mt5-bridge\\config\\mt4_terminal_map.json"
    Set-Content $envPath $envText -Encoding UTF8 -NoNewline
    Log "Updated MT4_TERMINAL_MAP_PATH in .env"
  }
}

Log "=== cache-mt4-brokers done ==="
Log "Note: brokers without a public CDN MT4 build (e.g. some IC Markets / Tickmill variants) still need File -> Open Account -> Scan once on the VPS."
