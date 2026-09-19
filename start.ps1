param(
    [switch]$SkipTailscale
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host ".env does not exist. Running first-time setup..." -ForegroundColor Yellow
    & "$PSScriptRoot\setup.ps1" -SkipTailscale:$SkipTailscale
    exit $LASTEXITCODE
}

docker compose up -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed."
}

if (-not $SkipTailscale -and (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    try {
        tailscale status *> $null
        if ($LASTEXITCODE -eq 0) {
            tailscale serve --bg 3000 *> $null
        }
    } catch {
        # Docker stack still works locally if Tailscale is offline.
    }
}

Write-Host "Local AI: http://127.0.0.1:3000" -ForegroundColor Green
if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    tailscale serve status
}
