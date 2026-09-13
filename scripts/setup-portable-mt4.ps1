# Install / refresh portable MetaTrader 4 for Finhub investor-password sync.
$ErrorActionPreference = "Continue"
function Log($m) { Write-Host ("{0} {1}" -f (Get-Date -Format o), $m) }

$Dst = "C:\finhubkh\mt4-portable"
$Setup = "C:\finhubkh\mt4setup.exe"
$SetupUrl = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt4/mt4setup.exe"
$BridgeEnv = "C:\finhubkh\finhubkh-mt5-bridge\.env"
$EaSrc = "C:\finhubkh\FinhubJournal_BridgeExport.mq4"

Log "=== setup-portable-mt4 begin ==="

Get-Process terminal -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2

if (-not (Test-Path $Setup)) {
  Log "Downloading MT4 setup..."
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $SetupUrl -OutFile $Setup -UseBasicParsing
}
Log ("Setup size={0}" -f (Get-Item $Setup).Length)

$Candidates = @(
  "C:\Program Files (x86)\MetaTrader 4",
  "C:\Program Files\MetaTrader 4",
  "C:\Program Files (x86)\MetaTrader",
  "C:\Program Files\MetaTrader"
)
$Src = $null
foreach ($c in $Candidates) {
  $term = Join-Path $c "terminal.exe"
  if (Test-Path $term) { $Src = $c; break }
}

if (-not $Src) {
  Log "No existing MT4 found - running installer /auto"
  $p = Start-Process -FilePath $Setup -ArgumentList "/auto" -PassThru -Wait
  Log ("Installer exit={0}" -f $p.ExitCode)
  Start-Sleep 5
  foreach ($c in $Candidates) {
    $term = Join-Path $c "terminal.exe"
    if (Test-Path $term) { $Src = $c; break }
  }
  if (-not $Src) {
    $found = Get-ChildItem "C:\Program Files (x86)","C:\Program Files","C:\Users" -Recurse -Filter terminal.exe -ErrorAction SilentlyContinue |
      Where-Object { $_.DirectoryName -match "MetaTrader|MT4" } |
      Select-Object -First 1
    if ($found) { $Src = $found.DirectoryName }
  }
}

if (-not $Src) {
  throw "MT4 install failed - terminal.exe not found"
}

Log ("Refreshing portable MT4 from {0} -> {1}" -f $Src, $Dst)
New-Item -ItemType Directory -Force -Path $Dst | Out-Null
robocopy $Src $Dst /E /XO /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
Set-Content (Join-Path $Dst "portable.ini") "" -Encoding ASCII

$Experts = Join-Path $Dst "MQL4\Experts"
$FilesDir = Join-Path $Dst "MQL4\Files"
New-Item -ItemType Directory -Force -Path $Experts | Out-Null
New-Item -ItemType Directory -Force -Path $FilesDir | Out-Null

$EaDst = Join-Path $Experts "FinhubJournal_BridgeExport.mq4"
if (Test-Path $EaSrc) {
  Copy-Item $EaSrc $EaDst -Force
  Log "Copied companion EA source"
}

$MetaEditor = Join-Path $Dst "metaeditor.exe"
if ((Test-Path $MetaEditor) -and (Test-Path $EaDst)) {
  Log "Compiling companion EA..."
  $compileArg = "/compile:$EaDst"
  Start-Process -FilePath $MetaEditor -ArgumentList $compileArg -Wait -NoNewWindow
  $Ex4 = Join-Path $Experts "FinhubJournal_BridgeExport.ex4"
  if (Test-Path $Ex4) {
    Log "Compiled FinhubJournal_BridgeExport.ex4"
  } else {
    Log "WARNING: compile did not produce .ex4 - compile once in MetaEditor UI"
  }
}

if (Test-Path $BridgeEnv) {
  $envText = Get-Content $BridgeEnv -Raw
  $line = "MT4_TERMINAL_PATH=C:\\finhubkh\\mt4-portable\\terminal.exe"
  if ($envText -match "MT4_TERMINAL_PATH=") {
    $envText = $envText -replace "MT4_TERMINAL_PATH=.*", $line
  } else {
    $extra = @"

# Portable MT4 for investor-password sync
$line
MT4_INIT_TIMEOUT_MS=45000
MT4_LOCK_KEY=finhubkh:mt4:terminal_lock
MT4_LOCK_TTL_SECONDS=900
MT4_LOCK_WAIT_SECONDS=180
"@
    $envText = $envText.TrimEnd() + $extra
  }
  if ($envText -notmatch "MT4_INIT_TIMEOUT_MS=") {
    $envText = $envText.TrimEnd() + "`r`nMT4_INIT_TIMEOUT_MS=45000`r`n"
  }
  Set-Content $BridgeEnv $envText -Encoding ASCII
  Log "Updated bridge .env with MT4_TERMINAL_PATH"
}

Log "=== setup-portable-mt4 done ==="
Log "ONE-TIME RDP: open portable terminal, attach FinhubJournal_BridgeExport EA to a chart, enable AutoTrading, save default profile."
