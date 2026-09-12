Param(
  [int]$BackendPort = 8011,
  [int]$FrontendPort = 5500
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$runtimeDir = Join-Path $root "data\\runtime"
$logDir = Join-Path $runtimeDir "tunnels"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$cloudflared = "C:\\Users\\LEE\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
  throw "cloudflared executable not found: $cloudflared"
}

function Ensure-ProcessByCommandLine {
  param(
    [string]$MatchText,
    [scriptblock]$StartAction
  )
  $existing = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$MatchText*" } | Select-Object -First 1
  if ($existing) {
    return $existing.ProcessId
  }
  & $StartAction
  Start-Sleep -Seconds 2
  $started = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$MatchText*" } | Select-Object -First 1
  if (-not $started) {
    throw "Failed to start process for: $MatchText"
  }
  return $started.ProcessId
}

$backendPid = Ensure-ProcessByCommandLine -MatchText "uvicorn main:app --host 127.0.0.1 --port $BackendPort" -StartAction {
  Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","main:app","--host","127.0.0.1","--port",$BackendPort -WorkingDirectory $backendDir -WindowStyle Hidden | Out-Null
}

$frontendPid = Ensure-ProcessByCommandLine -MatchText "http.server $FrontendPort" -StartAction {
  Start-Process -FilePath "python" -ArgumentList "-m","http.server",$FrontendPort,"--bind","127.0.0.1" -WorkingDirectory $frontendDir -WindowStyle Hidden | Out-Null
}

try {
  Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/health" -UseBasicParsing -TimeoutSec 8 | Out-Null
} catch {
  throw "Backend health check failed on http://127.0.0.1:$BackendPort/health"
}

try {
  Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/index.html" -UseBasicParsing -TimeoutSec 8 | Out-Null
} catch {
  throw "Frontend health check failed on http://127.0.0.1:$FrontendPort/index.html"
}

$backendTunnelLog = Join-Path $logDir "backend-tunnel.log"
$frontendTunnelLog = Join-Path $logDir "frontend-tunnel.log"
$backendTunnelErr = Join-Path $logDir "backend-tunnel.err.log"
$frontendTunnelErr = Join-Path $logDir "frontend-tunnel.err.log"
Set-Content -Path $backendTunnelLog -Value ""
Set-Content -Path $frontendTunnelLog -Value ""
Set-Content -Path $backendTunnelErr -Value ""
Set-Content -Path $frontendTunnelErr -Value ""

Start-Process -FilePath $cloudflared `
  -ArgumentList @("tunnel","--url","http://127.0.0.1:$BackendPort","--loglevel","info") `
  -WorkingDirectory $root `
  -RedirectStandardOutput $backendTunnelLog `
  -RedirectStandardError $backendTunnelErr `
  -WindowStyle Hidden | Out-Null

Start-Process -FilePath $cloudflared `
  -ArgumentList @("tunnel","--url","http://127.0.0.1:$FrontendPort","--loglevel","info") `
  -WorkingDirectory $root `
  -RedirectStandardOutput $frontendTunnelLog `
  -RedirectStandardError $frontendTunnelErr `
  -WindowStyle Hidden | Out-Null

function Get-TunnelUrl {
  param([string[]]$Paths, [int]$TimeoutSec = 30)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $regex = "https://[a-zA-Z0-9-]+\.trycloudflare\.com"
  while ((Get-Date) -lt $deadline) {
    foreach ($path in $Paths) {
      $text = Get-Content -Path $path -Raw -ErrorAction SilentlyContinue
      if ($text -match $regex) {
        return $Matches[0]
      }
    }
    Start-Sleep -Milliseconds 500
  }
  return ""
}

$backendUrl = Get-TunnelUrl -Paths @($backendTunnelLog, $backendTunnelErr)
$frontendUrl = Get-TunnelUrl -Paths @($frontendTunnelLog, $frontendTunnelErr)

if (-not $backendUrl -or -not $frontendUrl) {
  throw "Tunnel URL generation failed. Check logs in $logDir"
}

$encodedApi = [Uri]::EscapeDataString($backendUrl)
$frontendWithApi = "$frontendUrl/?api=$encodedApi"
$frontendViewerUrl = "$frontendUrl/?api=$encodedApi&role=ceo"

Write-Host ""
Write-Host "=== Test Environment Ready ==="
Write-Host "Backend Local : http://127.0.0.1:$BackendPort"
Write-Host "Frontend Local: http://127.0.0.1:$FrontendPort/index.html"
Write-Host "Backend URL   : $backendUrl"
Write-Host "Frontend URL  : $frontendUrl"
Write-Host "Admin URL     : $frontendWithApi"
Write-Host "Viewer URL    : $frontendViewerUrl"
Write-Host ""
Write-Host "Backend PID   : $backendPid"
Write-Host "Frontend PID  : $frontendPid"
Write-Host "Tunnel logs   : $logDir"
