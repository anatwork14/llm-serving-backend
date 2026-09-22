param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$Disable,
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$firewallRuleName = "LLM Serving Backend API"

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-IsAdministrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
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

function Set-DotEnvValue([string]$Name, [string]$Value) {
    $lines = @()
    if (Test-Path ".env") {
        $lines = @(Get-Content ".env")
    }

    $prefix = "$Name="
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith($prefix)) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $lines += "$Name=$Value"
    }

    Set-Content -Path ".env" -Value $lines -Encoding utf8
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
    $contexts = Invoke-DockerCommand -Arguments @("context", "ls", "--format", "{{.Name}}")
    if ($contexts.ExitCode -ne 0) {
        return
    }

    $contextNames = @(
        $contexts.Output -split "\r?\n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )

    if ($contextNames -contains "desktop-linux") {
        $env:DOCKER_CONTEXT = "desktop-linux"
    }
}

function Wait-ForDockerLinuxEngine([int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""

    Write-Host "Checking Docker Desktop Linux engine..." -ForegroundColor Cyan

    while ((Get-Date) -lt $deadline) {
        $probe = Invoke-DockerCommand -Arguments @("info", "--format", "{{.OSType}}")
        if ($probe.ExitCode -eq 0) {
            $osType = $probe.Output.Trim().ToLowerInvariant()
            if ($osType -eq "linux") {
                Write-Host "[ok] Docker Linux engine is ready." -ForegroundColor Green
                return
            }
            if ($osType -eq "windows") {
                throw @"
Docker Desktop is running Windows containers, but this project needs Linux containers.

Open Docker Desktop and switch to Linux containers, then rerun:
  .\public-api.ps1 -Port $Port
"@
            }
        } elseif ($probe.Output) {
            $lastError = $probe.Output
        }
        Start-Sleep -Seconds 2
    }

    if ($lastError) {
        Write-Host ""
        Write-Host "Last Docker error:" -ForegroundColor Yellow
        Write-Host $lastError
    }

    throw @"
Docker Desktop's Linux engine is not reachable.

Open Docker Desktop and wait until it says the engine is running. Then verify:
  docker info

If Docker Desktop is already open, try:
  wsl --shutdown

Then restart Docker Desktop and rerun:
  .\public-api.ps1 -Port $Port
"@
}

function Restart-Backend {
    Write-Host ""
    Write-Host "Recreating FastAPI gateway..." -ForegroundColor Cyan
    $result = Invoke-DockerCommand -Arguments @(
        "compose", "up", "-d", "--build", "--force-recreate", "backend"
    )
    if ($result.Output) {
        Write-Host $result.Output
    }
    if ($result.ExitCode -ne 0) {
        throw "docker compose failed while recreating the backend."
    }
}

function Wait-ForGateway([int]$HostPort, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $backendKey = Get-DotEnvValue "BACKEND_API_KEY"
    if ([string]::IsNullOrWhiteSpace($backendKey)) {
        throw "BACKEND_API_KEY is missing from .env."
    }

    while ((Get-Date) -lt $deadline) {
        try {
            $headers = @{ Authorization = "Bearer $backendKey" }
            $models = Invoke-RestMethod -Uri "http://127.0.0.1:$HostPort/v1/models" -Headers $headers -TimeoutSec 5
            if ($models.object -eq "list") {
                return
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "Gateway did not become reachable on localhost:$HostPort within $TimeoutSeconds seconds."
}

if (-not (Test-Command "docker")) {
    throw "Docker CLI was not found. Install/start Docker Desktop first."
}

if (-not (Test-Path ".env")) {
    throw ".env does not exist. Run .\setup.ps1 first."
}

Select-DockerDesktopLinuxContext
Wait-ForDockerLinuxEngine -TimeoutSeconds 60

if ($Disable) {
    $currentPort = Get-DotEnvValue "API_PORT"
    if ([string]::IsNullOrWhiteSpace($currentPort)) { $currentPort = "8000" }

    Set-DotEnvValue "API_BIND_ADDRESS" "127.0.0.1"
    Set-DotEnvValue "API_PORT" $currentPort

    if (-not $SkipFirewall) {
        if (Test-IsAdministrator) {
            Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue |
                Remove-NetFirewallRule -ErrorAction SilentlyContinue
        } else {
            Write-Host "[warn] Run PowerShell as Administrator to remove the Windows Firewall rule." -ForegroundColor Yellow
        }
    }

    Restart-Backend
    Wait-ForGateway -HostPort ([int]$currentPort)

    Write-Host ""
    Write-Host "[ok] Public API access disabled." -ForegroundColor Green
    Write-Host "Gateway is now localhost-only: http://127.0.0.1:$currentPort/v1"
    exit 0
}

if (-not $SkipFirewall -and -not (Test-IsAdministrator)) {
    throw @"
Opening Windows Firewall requires Administrator privileges.

Open PowerShell with "Run as administrator", then run:
  .\public-api.ps1 -Port $Port

Or, if you already manage the firewall yourself:
  .\public-api.ps1 -Port $Port -SkipFirewall
"@
}

Set-DotEnvValue "API_BIND_ADDRESS" "0.0.0.0"
Set-DotEnvValue "API_PORT" ([string]$Port)

if (-not $SkipFirewall) {
    Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private | Out-Null

    Write-Host "[ok] Windows Firewall allows TCP $Port on Private networks." -ForegroundColor Green
}

Restart-Backend
Wait-ForGateway -HostPort $Port

$localIps = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -ne "127.0.0.1" -and
            $_.IPAddress -notlike "169.254.*"
        } |
        Select-Object -ExpandProperty IPAddress -Unique
)

$publicIp = $null
try {
    $publicIp = ([string](Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 10)).Trim()
} catch {
    # Internet lookup is best-effort. Router setup can still proceed using the LAN IP.
}

Write-Host ""
Write-Host "=== Public API host configuration ===" -ForegroundColor Cyan
Write-Host "[ok] Docker publishes FastAPI on 0.0.0.0:$Port" -ForegroundColor Green
Write-Host "[ok] Authenticated /v1/models smoke test passed." -ForegroundColor Green
Write-Host ""
Write-Host "LAN addresses:" -ForegroundColor Cyan
foreach ($ip in $localIps) {
    Write-Host ("  http://{0}:{1}/v1" -f $ip, $Port)
}

if ($publicIp) {
    Write-Host ""
    Write-Host "Current Internet-visible IPv4: $publicIp" -ForegroundColor Cyan
    Write-Host "After router port forwarding, the candidate public API URL is:"
    Write-Host ("  http://{0}:{1}/v1" -f $publicIp, $Port) -ForegroundColor Green
}

Write-Host ""
Write-Host "Router step still required:" -ForegroundColor Yellow
Write-Host "  1. Reserve this Windows PC's LAN IP with DHCP reservation."
Write-Host "  2. Forward TCP WAN port $Port -> this Windows PC LAN IP port $Port."
Write-Host "  3. Compare the router WAN IPv4 with the Internet-visible IPv4 above."
Write-Host "     If they differ or the router WAN address is private/100.64.0.0/10, ask the ISP for public IPv4 or use a tunnel."
Write-Host ""
Write-Host "Client authentication:" -ForegroundColor Cyan
Write-Host "  Use BACKEND_API_KEY from .env as: Authorization: Bearer <key>"
Write-Host ""
Write-Host "Do NOT forward ports 8080 (raw llama.cpp) or 5432 (PostgreSQL)." -ForegroundColor Yellow
Write-Host "WARNING: direct HTTP public access is not encrypted. Use HTTPS/VPN for long-lived Internet exposure." -ForegroundColor Yellow
Write-Host ""
Write-Host "Disable remote binding later with:"
Write-Host "  .\public-api.ps1 -Disable"
