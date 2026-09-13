$ErrorActionPreference = "Continue"
function Log($m) { Write-Host $m }

Log "egress google"
try { Log ((Invoke-WebRequest -UseBasicParsing https://www.google.com -TimeoutSec 8).StatusCode) } catch { Log $_.Exception.Message }

Log "tcp tests"
foreach ($pair in @(
  @{H="1.1.1.1"; P=443},
  @{H="8.8.8.8"; P=443},
  @{H="169.50.136.170"; P=443},
  @{H="169.50.136.170"; P=4433},
  @{H="169.50.136.170"; P=1950}
)) {
  $t = New-Object Net.Sockets.TcpClient
  try {
    $iar = $t.BeginConnect($pair.H, $pair.P, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(5000, $false)
    if ($ok) {
      $t.EndConnect($iar)
      Log ("OK {0}:{1}" -f $pair.H, $pair.P)
    } else {
      Log ("TIMEOUT {0}:{1}" -f $pair.H, $pair.P)
    }
  } catch {
    Log ("FAIL {0}:{1} {2}" -f $pair.H, $pair.P, $_.Exception.Message)
  } finally {
    try { $t.Close() } catch {}
  }
}

Log "done"
