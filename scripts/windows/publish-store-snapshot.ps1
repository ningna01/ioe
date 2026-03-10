Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
$logDir = Join-Path $repoRoot 'logs'
$logFile = Join-Path $logDir 'store-snapshot-sync.log'

# Git identity for automated commits (used by Task Scheduler)
$gitUserEmail = 'gong17779211256@gmail.com'
$gitUserName  = 'ningna01'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$timestamp] $Message"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

function Invoke-Git {
    param([string[]]$Arguments)

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Push-Location $repoRoot
try {
    Write-Log "Starting store snapshot publish run."

    $statusOutput = (& git status --porcelain).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'git status failed.'
    }
    if ($statusOutput) {
        Write-Log "Skipping publish because the worktree is not clean."
        exit 0
    }

    & git ls-remote origin HEAD *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Skipping publish because GitHub is unreachable or authentication failed."
        exit 0
    }

    Invoke-Git -Arguments @('pull', '--rebase')

    & python manage.py export_store_snapshot
    if ($LASTEXITCODE -ne 0) {
        throw 'export_store_snapshot failed.'
    }

    $snapshotStatus = (& git status --porcelain -- db/store_snapshot.json).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'git status for store_snapshot.json failed.'
    }
    if (-not $snapshotStatus) {
        Write-Log "No snapshot change detected. Nothing to commit."
        exit 0
    }

    Invoke-Git -Arguments @('add', 'db/store_snapshot.json')
    $commitMessage = "chore(data): update store snapshot $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Invoke-Git -Arguments @('-c', "user.email=$gitUserEmail", '-c', "user.name=$gitUserName", 'commit', '-m', $commitMessage)
    Invoke-Git -Arguments @('push')

    Write-Log "Store snapshot publish completed successfully."
}
finally {
    Pop-Location
}
