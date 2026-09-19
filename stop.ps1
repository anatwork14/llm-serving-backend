$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

docker compose down
if ($LASTEXITCODE -ne 0) {
    throw "docker compose down failed."
}

Write-Host "LLM stack stopped." -ForegroundColor Green
Write-Host "Persistent model, database, and Open WebUI data were kept."
