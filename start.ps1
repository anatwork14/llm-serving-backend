param(
    [switch]$SkipTailscale
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-DockerCommand([string[]]$Arguments) {
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
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        if ($nativePreferenceExists) {
            $PSNativeCommandUseErrorActionPreference = $oldNativePreference
        }
    }

    $normalizedOutput = @(
        foreach ($item in @($output)) {
            if ($item -is [System.Management.Automation.ErrorRecord]) {
                $item.Exception.Message
            } else {
                [string]$item
            }
        }
    )

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = (($normalizedOutput -join [Environment]::NewLine).Trim())
    }
}

function Invoke-DockerLive([string[]]$Arguments) {\n    # Start-Process lets Docker inherit this console directly. This keeps build\n    # progress visible and avoids Windows PowerShell 5.1 turning stderr warnings\n    # into terminating NativeCommandError records.\n    $process = Start-Process -FilePath "docker" -ArgumentList $Arguments -NoNewWindow -Wait -PassThru\n    return $process.ExitCode\n}\n\nfunction Get-TailscaleExe {
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

    $contexts = Invoke-DockerCommand -Arguments @(
        "context",
        "ls",
        "--format",
        "{{.Name}}"
    )

    if ($contexts.ExitCode -eq 0) {
        $contextNames = @(
            $contexts.Output -split "\r?\n" |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ }
        )
        if ($contextNames -contains "desktop-linux") {
            $env:DOCKER_CONTEXT = "desktop-linux"
        }
    }
}

Select-DockerDesktopLinuxContext

if (-not (Test-Path ".env")) {
    Write-Host ".env does not exist. Running first-time setup..." -ForegroundColor Yellow
    & "$PSScriptRoot\setup.ps1" -SkipTailscale:$SkipTailscale
    exit $LASTEXITCODE
}

$dockerInfo = Invoke-DockerCommand -Arguments @("info")
if ($dockerInfo.ExitCode -ne 0) {
    if ($dockerInfo.Output) {
        Write-Host $dockerInfo.Output -ForegroundColor Yellow
    }
    throw "Docker's Linux engine is not ready. Run .\setup.ps1 for diagnostics."
}

# Always rebuild local images so a preceding git pull cannot leave the
# backend/entrypoint running stale source. Docker layer caching keeps this fast
# when nothing changed; model files remain in the persistent model-data volume.
Write-Host ""
Write-Host "Starting Docker stack..." -ForegroundColor Cyan
$composeExitCode = Invoke-DockerLive -Arguments @("compose", "up", "-d", "--build")
if ($composeExitCode -ne 0) {
    Write-Host ""
    Write-Host "Startup failed. Current container state:" -ForegroundColor Red
    $status = Invoke-DockerCommand -Arguments @("compose", "ps", "-a")
    if ($status.Output) {
        Write-Host $status.Output
    }

    Write-Host ""
    Write-Host "Recent backend/LLM logs:" -ForegroundColor Yellow
    $logs = Invoke-DockerCommand -Arguments @(
        "compose", "logs", "--no-color", "--tail", "100", "backend", "llm"
    )
    if ($logs.Output) {
        Write-Host $logs.Output
    }

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
