param(
    [string]$SourcePath = $PSScriptRoot,
    [string]$TargetPath = "D:\sisyphus",
    [string]$RemoteHost = "armin@192.168.2.117",
    [string]$RemotePath = "/volume1/docker/sisyphus"
)

Write-Host "Sisyphus — Publish to Synology" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "Source: $SourcePath"
Write-Host "Target: $TargetPath"
Write-Host "Remote: $RemoteHost`:$RemotePath"
Write-Host ""

# Validate source directory exists
if (-not (Test-Path $SourcePath)) {
    Write-Host "Error: Source directory not found at $SourcePath" -ForegroundColor Red
    exit 1
}

# Create local staging directory if needed
if (-not (Test-Path $TargetPath)) {
    Write-Host "Creating local staging directory..." -ForegroundColor Green
    try {
        New-Item -ItemType Directory -Path $TargetPath -Force | Out-Null
        Write-Host "Created: $TargetPath" -ForegroundColor Green
    }
    catch {
        Write-Host "Error creating staging directory: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Copying files to local staging..." -ForegroundColor Green
Write-Host ""

$robocopyArgs = @(
    "`"$SourcePath`"",
    "`"$TargetPath`"",
    "/E",           # Copy subdirectories, including empty ones
    "/XO",          # Exclude older files (skip unchanged)
    "/XF",          # Exclude files:
    ".env",         #   runtime secrets — never overwrite
    "*.db",         #   SQLite databases
    "*.db-shm",     #   SQLite WAL files
    "*.db-wal",
    "publish.ps1",  #   this script
    ".gitignore",
    ".dockerignore",
    "/XD",          # Exclude directories:
    "data",         #   persistent data (chroma vectors, db)
    "__pycache__",
    ".git",
    ".vscode",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    "/NP",          # No progress bar
    "/R:3",         # Retry 3 times on failure
    "/W:3"          # Wait 3 s between retries
)

$robocopyCommand = "robocopy.exe $($robocopyArgs -join ' ')"
Write-Host "Executing: $robocopyCommand" -ForegroundColor Gray

$process = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -NoNewWindow -Wait -PassThru

# Robocopy exit codes 0-3 are success (0=no change, 1=copied, 2=extra, 3=both)
if ($process.ExitCode -le 3) {
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Green
    Write-Host "Local staging OK  (exit $($process.ExitCode))" -ForegroundColor Green
    Write-Host "==============================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Preserved on remote (not overwritten):" -ForegroundColor Cyan
    Write-Host "  .env                 (secrets)"        -ForegroundColor Cyan
    Write-Host "  data/                (chroma + sqlite)" -ForegroundColor Cyan
    Write-Host "  *.db / *.db-*        (database files)" -ForegroundColor Cyan
    Write-Host ""

    $confirmation = Read-Host "Deploy to Synology now? (y/N)"
    if ($confirmation -ne 'y' -and $confirmation -ne 'Y') {
        Write-Host "Remote deployment cancelled." -ForegroundColor Yellow
        exit 0
    }

    Write-Host ""
    Write-Host "--- Syncing files to $RemoteHost`:$RemotePath ---" -ForegroundColor Green

    # rsync the staged folder to Synology
    # --exclude=data keeps the live data directory untouched on the NAS
    $rsyncArgs = @(
        "-avz",
        "--delete",
        "--exclude=data/",
        "--exclude=.env",
        "--exclude=*.db",
        "--exclude=*.db-shm",
        "--exclude=*.db-wal",
        "$TargetPath/",
        "$RemoteHost`:$RemotePath/"
    )

    Write-Host "rsync $($rsyncArgs -join ' ')" -ForegroundColor Gray
    $rsyncProcess = Start-Process -FilePath "rsync" -ArgumentList $rsyncArgs -NoNewWindow -Wait -PassThru

    if ($rsyncProcess.ExitCode -ne 0) {
        Write-Host "ERROR: rsync failed (exit $($rsyncProcess.ExitCode))." -ForegroundColor Red
        Write-Host "Tip: ensure rsync is available (e.g. via Git for Windows or WSL) and SSH keys are set up." -ForegroundColor Yellow
        exit 1
    }

    Write-Host ""
    Write-Host "--- Remote docker compose restart ---" -ForegroundColor Green

    $sshCommand = "cd $RemotePath && sudo -n /usr/local/bin/docker compose down && sudo -n /usr/local/bin/docker compose build sisyphus --no-cache && sudo -n /usr/local/bin/docker compose up -d"

    Write-Host "SSH: $sshCommand" -ForegroundColor Gray

    try {
        $sshProcess = Start-Process -FilePath "ssh.exe" -ArgumentList @($RemoteHost, $sshCommand) -NoNewWindow -Wait -PassThru

        if ($sshProcess.ExitCode -eq 0) {
            Write-Host ""
            Write-Host "==============================" -ForegroundColor Green
            Write-Host "Deployment complete!" -ForegroundColor Green
            Write-Host "==============================" -ForegroundColor Green
            Write-Host ""
            Write-Host "Dashboard: http://$($RemoteHost.Split('@')[1]):7070" -ForegroundColor Cyan
            Write-Host "Logs:      ssh $RemoteHost 'docker logs -f sisyphus'" -ForegroundColor Cyan
        }
        else {
            Write-Host "ERROR: SSH command failed (exit $($sshProcess.ExitCode))." -ForegroundColor Red
            exit 1
        }
    }
    catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Red
    Write-Host "ERROR: robocopy failed (exit $($process.ExitCode))" -ForegroundColor Red
    Write-Host "==============================" -ForegroundColor Red
    exit 1
}
