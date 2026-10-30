# FlowForge weekday morning start-up on the operator's Windows PC (docs/runbooks/morning.md).
# Run by a Windows Scheduled Task at 09:20 ET, Monday to Friday. Paper only; every step goes
# through the same guards as running it by hand. It never edits config or risk limits.
#   1. start Docker Desktop if it isn't running
#   2. start the IB Gateway (Mondays: approve the IBKR login on your phone)
#   3. kill switch: auto-release decides (only a node/watchdog/drill kill, only with 0 shares and
#      0 orders at IBKR; never an operator kill, never a halt; ADR-0017)
#   4. until reports/paper/drills-passed.json exists: run every paper drill once the tape is warm
#   5. start the watchdog and the paper node (PAPER_NODE_ARGS in .env picks shadow or trading)
# Everything is written to data\morning-<date>.log.
param([string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path)

$ErrorActionPreference = "Continue"
Set-Location $Repo
$log = Join-Path $Repo ("data\morning-" + (Get-Date -Format "yyyy-MM-dd") + ".log")
function Say($text) { $line = (Get-Date -Format "HH:mm:ss") + "  " + $text; Write-Output $line; Add-Content $log $line }
function EtNow { [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), "Eastern Standard Time") }

Say "FlowForge morning start in $Repo"
# the task's clock can be an hour off around a daylight-saving change: never start before 09:15 ET
while ((EtNow).TimeOfDay -lt [TimeSpan]"09:15:00") { Start-Sleep 30 }

# 1. Docker
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Say "Starting Docker Desktop"
    Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 5; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
    if ($LASTEXITCODE -ne 0) { Say "Docker did not start in 5 minutes: stopping"; exit 1 }
}

# 2. IB Gateway, up to 15 minutes for the Monday phone approval
docker compose up -d ib-gateway 2>&1 | ForEach-Object { Say $_ }
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    $state = docker inspect --format "{{.State.Health.Status}}" (docker compose ps -q ib-gateway) 2>$null
    if ($state -eq "healthy") { $healthy = $true; break }
    if ($i -eq 6) { Say "Waiting for the IBKR login: approve it on your phone if it asks" }
    Start-Sleep 10
}
if (-not $healthy) { Say "IB Gateway not logged in after 15 minutes: stopping (nothing traded)"; exit 1 }
Say "IB Gateway logged in"

# 3. kill switch
docker compose --profile ops run --rm auto-release 2>&1 | ForEach-Object { Say $_ }

# 4. drills, until they have all passed once
if (-not (Test-Path "reports\paper\drills-passed.json")) {
    while ((EtNow).TimeOfDay -lt [TimeSpan]"09:40:00") { Start-Sleep 30 }   # a few minutes of tape first
    Say "Running all paper drills (1 share of SPY each)"
    docker compose --profile paper stop node watchdog 2>&1 | Out-Null
    docker compose --profile drill run --rm paper-drill all 2>&1 | ForEach-Object { Say $_ }
    if ($LASTEXITCODE -ne 0) { Say "A drill failed or could not start: the bot stays off today. Read reports\paper."; exit 1 }
}

# 5. watchdog and node
docker compose --profile paper up -d watchdog node 2>&1 | ForEach-Object { Say $_ }
Say "Done: watchdog and paper node started"
