param(
    [switch]$SkipTailscale
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-TailscaleExe {
    $cmd = Get-Command "tailscale" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
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

# Always rebuild local images so a preceding git pull cannot leave the
# backend/entrypoint running stale source. Docker layer caching keeps this fast
# when nothing changed; model files remain in the persistent model-data volume.
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Startup failed. Current container state:" -ForegroundColor Red
    docker compose ps -a

    Write-Host ""
    Write-Host "Recent backend/LLM logs:" -ForegroundColor Yellow
    docker compose logs --no-color --tail 100 backend llm

    throw "docker compose up failed. Diagnostics are printed above."
}

$tailscaleExe = Get-TailscaleExe
if (-not $SkipTailscale -and $tailscaleExe) {
    try {
        & $tailscaleExe status *> $null
        if ($LASTEXITCODE -eq 0) {
            & $tailscaleExe serve --bg 3000 *> $null
        }
    } catch {
        # Docker stack still works locally if Tailscale is offline.
    }
}

Write-Host "Local AI: http://127.0.0.1:3000" -ForegroundColor Green
if ($tailscaleExe) {
    & $tailscaleExe serve status
} else {
    Write-Host "Tailscale was not found. Install the Windows app and sign in for remote access." -ForegroundColor Yellow
}
