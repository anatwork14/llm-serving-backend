param(
    [switch]$SkipModelTest
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

function Select-DockerDesktopLinuxContext {
    if (-not (Test-Command "docker")) {
        throw "Docker CLI was not found."
    }

    $contexts = Invoke-DockerCommand -Arguments @("context", "ls", "--format", "{{.Name}}")
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

function Get-DotEnvValue([string]$Name) {
    if (-not (Test-Path ".env")) {
        return $null
    }

    foreach ($line in Get-Content ".env") {
        if ($line -match "^\s*#") { continue }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) { continue }
        $key = $line.Substring(0, $separator).Trim()
        if ($key -eq $Name) {
            return $line.Substring($separator + 1).Trim()
        }
    }

    return $null
}

function Get-TailscaleExe {
    $cmd = Get-Command "tailscale" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }

    return $null
}

function Show-Check([string]$Message) {
    Write-Host "[ok] $Message" -ForegroundColor Green
}

function Show-Failure([string]$Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    $script:failed = $true
}

$failed = $false
Write-Host ""
Write-Host "=== LLM Serving doctor ===" -ForegroundColor Cyan
Write-Host "This checks the running stack without restarting containers." -ForegroundColor DarkGray
Write-Host ""

Select-DockerDesktopLinuxContext
$dockerInfo = Invoke-DockerCommand -Arguments @("info")
if ($dockerInfo.ExitCode -ne 0) {
    Show-Failure "Docker Linux engine is not reachable."
    if ($dockerInfo.Output) { Write-Host $dockerInfo.Output }
    exit 1
}
Show-Check "Docker Linux engine is reachable."

Write-Host ""
Write-Host "Container status:" -ForegroundColor Cyan
$composePs = Invoke-DockerCommand -Arguments @("compose", "ps")
if ($composePs.Output) { Write-Host $composePs.Output }
if ($composePs.ExitCode -ne 0) { Show-Failure "docker compose ps failed." }

try {
    $ready = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready" -Method Get -TimeoutSec 10
    if ($ready.status -eq "ok") {
        Show-Check "Backend readiness: database=$($ready.database), llama_cpp=$($ready.llama_cpp)."
    } else {
        Show-Failure "Backend readiness returned status '$($ready.status)'."
    }
} catch {
    Show-Failure "Backend readiness check failed: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "GPU visibility:" -ForegroundColor Cyan
$gpu = Invoke-DockerCommand -Arguments @(
    "compose", "exec", "-T", "llm", "nvidia-smi",
    "--query-gpu=name,memory.used,memory.total,utilization.gpu",
    "--format=csv,noheader,nounits"
)
if ($gpu.ExitCode -eq 0) {
    Show-Check $gpu.Output
} else {
    Show-Failure "The LLM container cannot query the NVIDIA GPU."
    if ($gpu.Output) { Write-Host $gpu.Output }
}

if (-not $SkipModelTest) {
    Write-Host ""
    Write-Host "End-to-end model smoke test:" -ForegroundColor Cyan
    $backendKey = Get-DotEnvValue "BACKEND_API_KEY"
    $modelAlias = Get-DotEnvValue "MODEL_ALIAS"
    $defaultUser = Get-DotEnvValue "DEFAULT_USER_ID"
    $allowedUsers = Get-DotEnvValue "ALLOWED_USER_IDS"
    $allowedRoles = Get-DotEnvValue "ALLOWED_ROLES"

    if ([string]::IsNullOrWhiteSpace($modelAlias)) { $modelAlias = "bonsai-2-27b" }
    $doctorUser = $defaultUser
    if (-not [string]::IsNullOrWhiteSpace($allowedUsers)) { $doctorUser = ($allowedUsers -split ",")[0].Trim() }
    if ([string]::IsNullOrWhiteSpace($doctorUser)) { $doctorUser = "dad" }
    $doctorRole = "user"
    if (-not [string]::IsNullOrWhiteSpace($allowedRoles)) { $doctorRole = ($allowedRoles -split ",")[0].Trim() }

    if ([string]::IsNullOrWhiteSpace($backendKey)) {
        Show-Failure "BACKEND_API_KEY was not found in .env."
    } else {
        $headers = @{
            Authorization = "Bearer $backendKey"
            "X-OpenWebUI-User-ID" = $doctorUser
            "X-OpenWebUI-User-Role" = $doctorRole
            "X-OpenWebUI-Task" = "doctor"
        }
        $body = @{
            model = $modelAlias
            stream = $false
            temperature = 0
            max_tokens = 8
            messages = @(@{ role = "user"; content = "Reply with exactly OK." })
        } | ConvertTo-Json -Depth 8 -Compress

        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/chat/completions" -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 120
            $text = ""
            if ($response.choices -and $response.choices.Count -gt 0) { $text = [string]$response.choices[0].message.content }
            if ([string]::IsNullOrWhiteSpace($text)) {
                Show-Failure "Model call returned no assistant text."
            } else {
                Show-Check "Gateway -> Prism -> Bonsai call succeeded. Response: $($text.Trim())"
            }
        } catch {
            Show-Failure "End-to-end model call failed: $($_.Exception.Message)"
        }
    }
}

Write-Host ""
Write-Host "Tailscale:" -ForegroundColor Cyan
$tailscaleExe = Get-TailscaleExe
if ($tailscaleExe) {
    $tailscaleStatus = & $tailscaleExe status 2>&1
    if ($LASTEXITCODE -eq 0) {
        Show-Check "Tailscale is connected."
        & $tailscaleExe serve status
    } else {
        Write-Host "[warn] Tailscale is installed but not connected." -ForegroundColor Yellow
        $tailscaleStatus | ForEach-Object { Write-Host $_ }
    }
} else {
    Write-Host "[warn] Tailscale is not installed/found. Local AI can still work." -ForegroundColor Yellow
}

Write-Host ""
if ($failed) {
    Write-Host "Doctor found one or more failures." -ForegroundColor Red
    Write-Host "Recent logs: docker compose logs --tail=100 backend llm" -ForegroundColor Yellow
    exit 1
}

Write-Host "All core checks passed." -ForegroundColor Green
