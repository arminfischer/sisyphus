param(
    [string]$TargetPath = "D:\sisyphus",
    [string]$LocalPath = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

$localDbPath = Join-Path $LocalPath "data\sisyphus.db"
$targetDataPath = Join-Path $TargetPath "data"
$targetDbPath = Join-Path $targetDataPath "sisyphus.db"

if (-not (Test-Path $localDbPath)) {
    throw "Local database not found at $localDbPath. Fetch production data first."
}
if (-not (Test-Path $targetDataPath)) {
    throw "Production data folder not found at $targetDataPath. Check that the NAS share is mapped."
}

Write-Warning "Stop the local agent and the production container before copying SQLite files."
$confirmation = Read-Host "Replace the production database with the local database? (y/N)"
if ($confirmation -notin @("y", "Y")) {
    Write-Host "Operation cancelled." -ForegroundColor Yellow
    exit 0
}

if (Test-Path $targetDbPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = "$targetDbPath.bak.$stamp"
    Copy-Item -LiteralPath $targetDbPath -Destination $backupPath
    foreach ($suffix in @("-wal", "-shm")) {
        $sidecar = "$targetDbPath$suffix"
        if (Test-Path $sidecar) {
            Copy-Item -LiteralPath $sidecar -Destination "$backupPath$suffix"
        }
    }
    Write-Host "Production backup created: $backupPath" -ForegroundColor Green
}

Copy-Item -LiteralPath $localDbPath -Destination $targetDbPath -Force
foreach ($suffix in @("-wal", "-shm")) {
    $sidecar = "$targetDbPath$suffix"
    if (Test-Path $sidecar) {
        Remove-Item -LiteralPath $sidecar -Force
    }
}
Write-Host "Local database copied to production: $targetDbPath" -ForegroundColor Green
