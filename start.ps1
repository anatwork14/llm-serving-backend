param(
    [switch]$SkipTailscale
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Select-DockerDesktopLinuxContext {
    if (-not (Test-Command "docker")) {
        throw "Docker CLI was not found."
    }

    $oldErrorActionPreference = $ErrorActionPreference
    $nativePreferenceExists = $null -ne (
        Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    )
    if ($nativePreferenceExists) {
        $oldNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        $ErrorActionPreference = "Continue"
        $contexts = & docker context ls --format "{{.Name}}" 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        if ($nativePreferenceExists) {
            $PSNativeCommandUseErrorActionPreference = $oldNativePreference
        }
    }

    if ($exitCode -eq 0 -and (@($contexts) -contains "desktop-linux")) {
        $env:DOCKER_CONTEXT = "desktop-linux"
    }
}

Select-DockerDesktopLinuxContext

if (-not (Test-Path ".env")) {
    Write-Host ".env does not exist. Running first-time setup..." -ForegroundColor Yellow
    & "$PSScriptRoot\setup.ps1" -SkipTailscale:$SkipTailscale
    exit $LASTEXITCODE
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker's Linux engine is not ready. Run .\setup.ps1 for diagnostics."
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
