param(
    [string]$TargetPath = "D:\sisyphus",
    [string]$LocalPath = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
$targetDbPath = Join-Path $TargetPath "data\sisyphus.db"
$localDataPath = Join-Path $LocalPath "data"
$localDbPath = Join-Path $localDataPath "sisyphus.db"

if (-not (Test-Path $targetDbPath)) {
    throw "Production database not found at $targetDbPath. Check that the NAS share is mapped."
}
if (-not (Test-Path $localDataPath)) {
    New-Item -ItemType Directory -Path $localDataPath -Force | Out-Null
}

Write-Warning "Stop the local agent and the production container before copying SQLite files."
$confirmation = Read-Host "After stopping both agents, replace the local database with production data? (y/N)"
if ($confirmation -notin @("y", "Y")) {
    Write-Host "Fetch cancelled." -ForegroundColor Yellow
    exit 0
}

if (Test-Path $localDbPath) {
    $backupPath = "$localDbPath.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $localDbPath -Destination $backupPath
    foreach ($suffix in @("-wal", "-shm")) {
        $sidecar = "$localDbPath$suffix"
        if (Test-Path $sidecar) {
            Copy-Item -LiteralPath $sidecar -Destination "$backupPath$suffix"
        }
    }
    Write-Host "Local backup created: $backupPath" -ForegroundColor Green
}

foreach ($suffix in @("-wal", "-shm")) {
    $sidecar = "$localDbPath$suffix"
    if (Test-Path $sidecar) {
        Remove-Item -LiteralPath $sidecar -Force
    }
}

Copy-Item -LiteralPath $targetDbPath -Destination $localDbPath -Force
Write-Host "Production database copied to $localDbPath" -ForegroundColor Green