$ErrorActionPreference = "SilentlyContinue"

$targets = @(
  "cloudflared.exe",
  "python.exe"
)

foreach ($name in $targets) {
  Get-Process -Name ($name -replace ".exe$","") | ForEach-Object {
    $pidValue = $_.Id
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue").CommandLine
    if ($name -eq "cloudflared.exe") {
      Stop-Process -Id $pidValue -Force
      return
    }
    if ($cmd -like "*uvicorn main:app*" -or $cmd -like "*http.server 5500*") {
      Stop-Process -Id $pidValue -Force
    }
  }
}

Write-Host "Test tunnel processes stopped."
