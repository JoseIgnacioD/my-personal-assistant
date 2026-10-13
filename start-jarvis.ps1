<#
.SYNOPSIS
  Starts Jarvis: checks n8n, starts the MCP server (and optionally the API), then the web app.

.EXAMPLE
  .\start-jarvis.ps1                      # MCP server + Chainlit web app
  .\start-jarvis.ps1 -Api                 # also the Jarvis API (needed by the n8n evaluation)
  .\start-jarvis.ps1 -Terminal            # terminal chat instead of the web app
  .\start-jarvis.ps1 -ModelProfile glm    # use another profile from config/models.yaml

  Stop everything with .\stop-jarvis.ps1
#>
param(
    [switch]$Api,
    [switch]$Terminal,
    [string]$ModelProfile = "",
    [string]$N8nUrl = "http://localhost:5678"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PidFile = Join-Path $Root ".jarvis-pids"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    $Message" -ForegroundColor Yellow }

function Test-Port([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-Port([int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# Reads a value from .env (falls back to a default)
function Get-EnvValue([string]$Name, [string]$Default) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
        if ($line) {
            $value = (($line -split "=", 2)[1] -split "\s+#")[0].Trim()
            if ($value) { return $value }
        }
    }
    return $Default
}

# Opens a new PowerShell window running a Python module, and remembers its process id
function Start-JarvisWindow([string]$Title, [string]$Arguments) {
    $command = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$Root'; & '$Python' $Arguments"
    $process = Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command", $command -PassThru
    Add-Content -Path $PidFile -Value $process.Id
    return $process
}

function Start-JarvisService([string]$Name, [int]$Port, [string]$Arguments, [int]$TimeoutSeconds) {
    Write-Step "Starting $Name (port $Port)"
    if (Test-Port $Port) {
        Write-Warn "Port $Port is already in use: assuming $Name is already running."
        return
    }
    Start-JarvisWindow "Jarvis - $Name" $Arguments | Out-Null
    if (Wait-Port $Port $TimeoutSeconds) {
        Write-Ok "$Name is ready."
    } else {
        Write-Host "    $Name did not start within $TimeoutSeconds s. Check its window for errors." -ForegroundColor Red
        exit 1
    }
}

# ------------------------------------------------------------------ checks
Write-Step "Checking the project"
if (-not (Test-Path $Python)) {
    Write-Host "    No virtual environment found. Create it first:" -ForegroundColor Red
    Write-Host "      python -m venv .venv"
    Write-Host "      .venv\Scripts\activate"
    Write-Host "      pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "    No .env file found. Copy .env.example to .env and fill it in." -ForegroundColor Red
    exit 1
}
Write-Ok "Virtual environment and .env found."

if ($ModelProfile) {
    $env:MODEL_PROFILE = $ModelProfile   # overrides MODEL_PROFILE from .env for this run
    Write-Ok "Model profile: $ModelProfile"
}

Write-Step "Checking n8n at $N8nUrl"
try {
    Invoke-WebRequest -Uri "$N8nUrl/healthz" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Ok "n8n is running."
} catch {
    Write-Warn "n8n is not reachable. Start n8n, or calendar, email and weather requests will fail."
}

if (Test-Path $PidFile) { Remove-Item $PidFile }

# ------------------------------------------------------------------ start
$McpPort = [int](Get-EnvValue "MCP_PORT" "8001")
Start-JarvisService "MCP server" $McpPort "-m mcp_server.server" 30

if ($Api) {
    $ApiPort = [int](Get-EnvValue "API_PORT" "8002")
    Start-JarvisService "Jarvis API" $ApiPort "-m api.main" 60
}

if ($Terminal) {
    Write-Step "Starting the terminal chat (type 'exit' to quit)"
    & $Python -m scripts.chat
    exit 0
}

Start-JarvisService "web app" 8000 "-m chainlit run app.py --headless --port 8000" 90
Start-Process "http://localhost:8000"

Write-Host ""
Write-Host "Jarvis is running: http://localhost:8000" -ForegroundColor Green
if ($Api) { Write-Host "Jarvis API:        http://localhost:$ApiPort/eval" -ForegroundColor Green }
Write-Host "Stop everything with: .\stop-jarvis.ps1"
