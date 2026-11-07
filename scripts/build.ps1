$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
# Docker Compose v5 Bake currently rejects non-ASCII session paths on this host.
# Individual Docker builds work with Cyrillic paths and reuse the same layer cache.
$serviceImages = [ordered]@{
    collector = 'collector'
    storage = 'storage'
    ml = 'ml_service'
    master = 'web_master'
    visualization = 'visualization'
}
foreach ($serviceName in $serviceImages.Keys) {
    $serviceFolder = $serviceImages[$serviceName]
    Write-Host "Building $serviceName..."
    docker build --provenance=false -f "$serviceFolder/Dockerfile" -t "kontur-hvac-${serviceName}:local" .
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $serviceName" }
}
