<#
.SYNOPSIS
  Stops everything started by start-jarvis.ps1 (MCP server, Jarvis API, web app).
#>
$Root = $PSScriptRoot
$PidFile = Join-Path $Root ".jarvis-pids"

Write-Host "==> Stopping Jarvis" -ForegroundColor Cyan

# 1. The windows opened by start-jarvis.ps1 (taskkill /T also stops the Python process inside)
if (Test-Path $PidFile) {
    foreach ($id in Get-Content $PidFile) {
        if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
            taskkill /PID $id /T /F | Out-Null
            Write-Host "    Stopped process tree $id" -ForegroundColor Green
        }
    }
    Remove-Item $PidFile
}

# 2. Anything still listening on the Jarvis ports (e.g. started by hand)
foreach ($port in 8000, 8001, 8002) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($owner in ($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -match "python") {
            Stop-Process -Id $owner -Force
            Write-Host "    Stopped $($process.ProcessName) on port $port" -ForegroundColor Green
        }
    }
}

Write-Host "Done. (n8n keeps running.)"
