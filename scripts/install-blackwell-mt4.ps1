# Install Blackwell MT4 server pack into portable terminal.
$ErrorActionPreference = "Continue"
function Log($m) { Write-Host ("{0} {1}" -f (Get-Date -Format o), $m) }

$Setup = "C:\finhubkh\blackwellglobal14setup.exe"
$Url = "https://download.mql5.com/cdn/web/blackwell.global.investments/mt4/blackwellglobal14setup.exe"
$Portable = "C:\finhubkh\mt4-portable"

Log "Downloading Blackwell MT4 setup"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not (Test-Path $Setup)) {
  Invoke-WebRequest -Uri $Url -OutFile $Setup -UseBasicParsing
}
Log ("Setup size={0}" -f (Get-Item $Setup).Length)

Log "Running installer /auto"
$p = Start-Process -FilePath $Setup -ArgumentList "/auto" -PassThru -Wait
Log ("Installer exit={0}" -f $p.ExitCode)
Start-Sleep 3

$found = Get-ChildItem "C:\Program Files (x86)","C:\Program Files","C:\Users" -Recurse -Filter "terminal.exe" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "Blackwell|BGT|bgifx" -or $_.DirectoryName -match "MetaTrader 4" } |
  Select-Object -First 5

foreach ($f in $found) { Log ("found {0}" -f $f.FullName) }

# Copy any .srv / servers.* into portable config
$cfg = Join-Path $Portable "config"
New-Item -ItemType Directory -Force -Path $cfg | Out-Null
Get-ChildItem "C:\Program Files (x86)","C:\Program Files","C:\Users\finhubkh_admin.FINHUBKH-MT5-BR\AppData\Roaming\MetaQuotes" -Recurse -Include *.srv,servers.dat,servers.ini -ErrorAction SilentlyContinue |
  ForEach-Object {
    try {
      Copy-Item $_.FullName (Join-Path $cfg $_.Name) -Force -ErrorAction SilentlyContinue
      Log ("copied {0}" -f $_.FullName)
    } catch {}
  }

Log "done"
