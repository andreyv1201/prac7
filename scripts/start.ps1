param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker Desktop is required. Install it, then run start.cmd again.'
    exit 1
}
$engineAvailable = $false
try {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    $engineAvailable = $LASTEXITCODE -eq 0
} catch { $engineAvailable = $false }
if (-not $engineAvailable) {
    $dockerDesktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path -LiteralPath $dockerDesktopPath) {
        Write-Host 'Starting Docker Desktop...'
        Start-Process -FilePath $dockerDesktopPath -WindowStyle Hidden
        $dockerReady = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            Start-Sleep -Seconds 2
            try {
                docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
            } catch { continue }
        }
        if (-not $dockerReady) { Write-Host 'Open Docker Desktop and wait for the engine to start.'; exit 1 }
    } else { Write-Host 'Start Docker Desktop first.'; exit 1 }
}
Write-Host 'Building and starting Kontur. The first build downloads Python and PyTorch.'
& (Join-Path $PSScriptRoot 'build.ps1')
docker compose up --no-build -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { Write-Host 'Startup failed. Run docker compose logs for details.'; exit 1 }
$interfacePort = '8050'
if ($env:UI_PORT) { $interfacePort = $env:UI_PORT }
elseif (Test-Path -LiteralPath '.env') {
    $portSetting = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^UI_PORT=\d+$' } | Select-Object -Last 1
    if ($portSetting) { $interfacePort = $portSetting.Split('=')[1] }
}
Write-Host "Ready: http://127.0.0.1:$interfacePort"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$interfacePort" }
