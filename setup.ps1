param(
    [switch]$SkipTailscale,
    [switch]$SkipGpuTest
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function New-HexSecret([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $rng.GetBytes($buffer)
    } finally {
        if ($null -ne $rng) {
            $rng.Dispose()
        }
    }

    # Windows PowerShell 5.1 / .NET Framework does not provide
    # Convert.ToHexString(), so format bytes explicitly.
    return -join ($buffer | ForEach-Object { $_.ToString("x2") })
}

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
    $contexts = Invoke-DockerCommand -Arguments @(
        "context",
        "ls",
        "--format",
        "{{.Name}}"
    )

    if ($contexts.ExitCode -ne 0) {
        return
    }

    $contextNames = @(
        $contexts.Output -split "\r?\n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )

    if ($contextNames -contains "desktop-linux") {
        # Keep this local to this PowerShell process. We do not modify the
        # user's global Docker context.
        $env:DOCKER_CONTEXT = "desktop-linux"
        Write-Host "[ok] Using Docker context: desktop-linux" -ForegroundColor Green
    }
}

function Wait-ForDockerLinuxEngine([int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""
    $lastOsType = ""

    Write-Host "Waiting for Docker's Linux engine..." -ForegroundColor Cyan

    while ((Get-Date) -lt $deadline) {
        $probe = Invoke-DockerCommand -Arguments @(
            "info",
            "--format",
            "{{.OSType}}"
        )

        if ($probe.ExitCode -eq 0) {
            $lastOsType = $probe.Output.Trim()

            if ($lastOsType -eq "linux") {
                Write-Host "[ok] Docker Linux engine is ready." -ForegroundColor Green
                return
            }

            if ($lastOsType -eq "windows") {
                throw @"
Docker Desktop is running Windows containers, but this project needs Linux containers.

Open Docker Desktop and switch to Linux containers, then run:
  .\setup.ps1
"@
            }
        } else {
            $lastError = $probe.Output
        }

        Start-Sleep -Seconds 3
    }

    Write-Host ""
    Write-Host "Docker Desktop is open, but its Linux engine is not reachable." -ForegroundColor Red

    if ($lastError) {
        Write-Host ""
        Write-Host "Last Docker error:" -ForegroundColor Yellow
        Write-Host $lastError
    }

    Write-Host ""
    Write-Host "Docker contexts:" -ForegroundColor Yellow
    $contextInfo = Invoke-DockerCommand -Arguments @("context", "ls")
    if ($contextInfo.Output) {
        Write-Host $contextInfo.Output
    }

    if (Test-Command "wsl") {
        Write-Host ""
        Write-Host "WSL status:" -ForegroundColor Yellow
        & wsl --status
        Write-Host ""
        Write-Host "WSL distributions:" -ForegroundColor Yellow
        & wsl -l -v
    }

    throw @"
Docker Desktop's UI is running, but the Linux/WSL2 Docker engine did not become ready.

In Docker Desktop:
  1. Settings -> General -> enable "Use the WSL 2 based engine".
  2. Make sure Docker is using Linux containers.

Then in PowerShell run:
  wsl --update
  wsl --shutdown

Restart Docker Desktop, then rerun:
  .\setup.ps1
"@
}

Write-Host ""
Write-Host "=== Local LLM one-time setup ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Command "docker")) {
    throw "Docker CLI was not found. Install Docker Desktop, enable its WSL2 backend, then rerun .\setup.ps1."
}

Select-DockerDesktopLinuxContext
Wait-ForDockerLinuxEngine -TimeoutSeconds 180

if (-not (Test-Path ".env")) {
    $backendKey = New-HexSecret
    $adminKey = New-HexSecret
    $webuiKey = New-HexSecret
    $llamaKey = New-HexSecret
    $dbPassword = New-HexSecret 24

    $envContent = @"
BACKEND_API_KEY=$backendKey
ADMIN_API_KEY=$adminKey
WEBUI_SECRET_KEY=$webuiKey

POSTGRES_USER=llm
POSTGRES_PASSWORD=$dbPassword
POSTGRES_DB=llm
DATABASE_URL=postgresql+asyncpg://llm:$dbPassword@db:5432/llm

LLAMA_BASE_URL=http://llm:8080/v1
LLAMA_API_KEY=$llamaKey
MODEL_ALIAS=bonsai-2-27b
UPSTREAM_MODEL=

HF_MODEL_REPO=prism-ml/Ternary-Bonsai-2-27B-gguf
MODEL_FILE=Ternary-Bonsai-2-27B-PQ2_0.gguf
MMPROJ_FILE=Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
LLM_CONTEXT_SIZE=16384
LLM_GPU_LAYERS=99
LLM_IMAGE_MAX_TOKENS=1024
LLM_ENABLE_VISION=true
PRISM_LLAMA_TAG=prism-b10709-9a9394a
PRISM_CUDA_VERSION=12.4

DEFAULT_USER_ID=dad
ALLOWED_USER_IDS=
ALLOWED_ROLES=user,admin
SYSTEM_PROMPT_PATH=config/system_prompt.txt
MEMORY_TOP_K=4
RAG_TOP_K=4
RECENT_MESSAGE_LIMIT=10
SUMMARY_TRIGGER_MESSAGES=20
SUMMARY_KEEP_RECENT=8
MAX_CONTEXT_ITEM_CHARS=1600

EMBEDDINGS_ENABLED=true
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_DEVICE=cpu

ALLOWED_TOOL_NAMES=
REQUEST_TIMEOUT_SECONDS=600
LOG_LEVEL=INFO
"@

    Set-Content -Path ".env" -Value $envContent -Encoding utf8
    Write-Host "[ok] Created .env with random local secrets." -ForegroundColor Green
} else {
    Write-Host "[ok] Existing .env kept unchanged." -ForegroundColor Green
}

if (-not $SkipGpuTest) {
    Write-Host ""
    Write-Host "Checking Docker GPU access..." -ForegroundColor Cyan

    $gpuTest = Invoke-DockerCommand -Arguments @(
        "run",
        "--rm",
        "--gpus",
        "all",
        "nvidia/cuda:12.4.1-base-ubuntu22.04",
        "nvidia-smi"
    )

    if ($gpuTest.Output) {
        Write-Host $gpuTest.Output
    }

    if ($gpuTest.ExitCode -ne 0) {
        throw @"
Docker's Linux engine is working, but the container could not access your NVIDIA GPU.

Update the NVIDIA Windows driver, make sure Docker Desktop uses WSL2, then test:
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
"@
    }
}

Write-Host ""
Write-Host "Starting the full stack..." -ForegroundColor Cyan

$composeUp = Invoke-DockerCommand -Arguments @(
    "compose",
    "up",
    "-d",
    "--build"
)

if ($composeUp.Output) {
    Write-Host $composeUp.Output
}

if ($composeUp.ExitCode -ne 0) {
    Write-Host ""
    Write-Host "Docker Compose failed. Collecting LLM diagnostics..." -ForegroundColor Red

    $ps = Invoke-DockerCommand -Arguments @("compose", "ps", "-a")
    if ($ps.Output) {
        Write-Host ""
        Write-Host "Container status:" -ForegroundColor Yellow
        Write-Host $ps.Output
    }

    $logs = Invoke-DockerCommand -Arguments @(
        "compose",
        "logs",
        "--no-color",
        "--tail",
        "200",
        "llm"
    )
    if ($logs.Output) {
        Write-Host ""
        Write-Host "Last 200 LLM log lines:" -ForegroundColor Yellow
        Write-Host $logs.Output
    }

    throw "docker compose up failed. The LLM diagnostics are printed above."
}

Write-Host ""
Write-Host "The first launch downloads about 8 GB of Bonsai model files." -ForegroundColor Yellow
Write-Host "Waiting for the web UI to become reachable..." -ForegroundColor Yellow

$ready = $false
$deadline = (Get-Date).AddMinutes(45)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {
        # Services are still downloading/loading.
    }

    Start-Sleep -Seconds 5
}

if (-not $ready) {
    Write-Host ""
    Write-Host "The stack is still starting. This is usually the first model download." -ForegroundColor Yellow
    Write-Host "Run: docker compose logs -f llm" -ForegroundColor Yellow
} else {
    Write-Host "[ok] Open WebUI is ready at http://127.0.0.1:3000" -ForegroundColor Green
}

if (-not $SkipTailscale -and (Test-Command "tailscale")) {
    try {
        tailscale status *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "Configuring private Tailscale access..." -ForegroundColor Cyan
            tailscale serve --bg 3000
            if ($LASTEXITCODE -eq 0) {
                tailscale serve status
            }
        }
    } catch {
        Write-Host "Tailscale is installed but not connected; skipping Tailscale Serve." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Local UI: http://127.0.0.1:3000"
Write-Host ""
Write-Host "On a fresh Open WebUI install, create your first account; it becomes the admin."
Write-Host "For Dad, re-enable signup in Admin Settings if needed, then let him create his own account."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  .\start.ps1"
Write-Host "  .\stop.ps1"
Write-Host "  docker compose logs -f llm"
Write-Host "  docker compose logs -f backend"
