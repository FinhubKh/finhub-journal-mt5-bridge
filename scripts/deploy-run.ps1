# One-shot remote deploy entrypoint uploaded by GitHub Actions.
# Keeps all logic in -File form so OpenSSH quoting cannot drop steps.
$ErrorActionPreference = "Stop"
$Log = "C:\finhubkh\deploy-run.log"

function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line -ErrorAction SilentlyContinue
  Write-Host $line
}

# Fresh log each deploy for easier CI debugging
if (Test-Path $Log) { Remove-Item $Log -Force -ErrorAction SilentlyContinue }

Log "=== deploy-run begin ==="

$staging = "C:\finhubkh\deploy-staging"
$tarball = "C:\finhubkh\bridge-deploy.tgz"
$keyPath = "C:\finhubkh\bridge-enc-key.txt"
$envPath = "C:\finhubkh\finhubkh-mt5-bridge\.env"
$deployRemote = "C:\finhubkh\deploy-remote.ps1"

if (-not (Test-Path $tarball)) { throw "Missing $tarball" }
if (-not (Test-Path $deployRemote)) { throw "Missing $deployRemote" }

# Upsert encryption key before services restart.
if (Test-Path $keyPath) {
  if (-not (Test-Path $envPath)) { throw "Missing $envPath" }
  $key = (Get-Content -Raw $keyPath).Trim()
  Remove-Item $keyPath -Force
  $lines = Get-Content $envPath
  $out = New-Object System.Collections.Generic.List[string]
  $found = $false
  foreach ($line in $lines) {
    if ($line -match "^INVESTOR_CRED_ENCRYPTION_KEY=") {
      [void]$out.Add("INVESTOR_CRED_ENCRYPTION_KEY=$key")
      $found = $true
    } else {
      [void]$out.Add($line)
    }
  }
  if (-not $found) { [void]$out.Add("INVESTOR_CRED_ENCRYPTION_KEY=$key") }
  Set-Content -Path $envPath -Value $out -Encoding ASCII
  Log "INVESTOR_CRED_ENCRYPTION_KEY upserted in .env"
} else {
  Log "No bridge-enc-key.txt - leaving .env encryption key unchanged"
}

Log "STEP extract-staging"
if (Test-Path $staging) {
  Log "Removing old staging"
  Remove-Item $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Log "Extracting tarball"
& tar.exe -xf $tarball -C $staging
if ($LASTEXITCODE -ne 0) { throw "tar extract failed code=$LASTEXITCODE" }
Log "Extract complete"

Log "STEP deploy-remote"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deployRemote
if ($LASTEXITCODE -ne 0) { throw "deploy-remote.ps1 failed code=$LASTEXITCODE" }
Log "deploy-remote finished"

# Prove new API code is on disk.
$routes = "C:\finhubkh\finhubkh-mt5-bridge\app\routes_jobs.py"
if (-not (Test-Path $routes)) { throw "Missing $routes after sync" }
$hit = Select-String -Path $routes -Pattern "workers_alive" -SimpleMatch -ErrorAction SilentlyContinue
if (-not $hit) { throw "routes_jobs.py missing workers_alive after sync - staging sync failed" }
Log "Verified workers_alive present in routes_jobs.py"

Log "=== deploy-run done ==="
