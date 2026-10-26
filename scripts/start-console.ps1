param(
    [ValidateSet('Postgres', 'Local')][string]$Database = 'Postgres',
    [ValidateRange(1,65535)][int]$Port = 8787,
    [ValidateRange(1,65535)][int]$DatabasePort = 55432,
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [switch]$NoMarket,
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$consoleRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$consoleRuntime = Join-Path $consoleRoot '.runtime'
$consolePython = Join-Path $consoleRoot '.venv/Scripts/python.exe'
$consoleUrl = "http://127.0.0.1:$Port"
$consolePidFile = Join-Path $consoleRuntime "server-$Port.json"

function Invoke-ConsoleCommand {
    param([string]$Executable, [string[]]$CommandArgs)
    & $Executable @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable (exit $LASTEXITCODE)" }
}

New-Item -ItemType Directory -Path $consoleRuntime -Force | Out-Null
if (Test-Path -LiteralPath $consolePidFile) {
    $consoleOld = Get-Content -LiteralPath $consolePidFile -Raw | ConvertFrom-Json
    if ($consoleOld.root -ne $consoleRoot -or $consoleOld.url -ne $consoleUrl) { throw 'Process record belongs to another checkout or port' }
    $consoleProcess = Get-Process -Id $consoleOld.pid -ErrorAction SilentlyContinue
    if ($consoleProcess -and $consoleProcess.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$consoleOld.started_at).UtcDateTime.Ticks) {
        if ($consoleOld.database -ne $Database) { throw 'This port is already running with another database mode. Stop it before switching.' }
        Write-Output "AetherTrade is already running: $($consoleOld.url)"
        if (-not $NoBrowser) { Start-Process $consoleOld.url }
        return
    }
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied. Choose -Port with a free local port."
}

Push-Location $consoleRoot
$consolePreviousDatabase = [Environment]::GetEnvironmentVariable('AETHERTRADE_DATABASE_URL', 'Process')
$consolePreviousPort = [Environment]::GetEnvironmentVariable('AETHERTRADE_PORT', 'Process')
$consolePreviousMarket = [Environment]::GetEnvironmentVariable('AETHERTRADE_MARKET_ENABLED', 'Process')
try {
    if (-not (Test-Path -LiteralPath $consolePython)) {
        Invoke-ConsoleCommand -Executable 'python' -CommandArgs @('-m', 'venv', '.venv')
    }
    if (-not $SkipInstall) {
        Invoke-ConsoleCommand -Executable $consolePython -CommandArgs @('-m', 'pip', 'install', '-e', '.')
        $consoleLockHash = (Get-FileHash -LiteralPath 'web/package-lock.json' -Algorithm SHA256).Hash
        $consoleLockRecord = Join-Path $consoleRuntime 'web-lock.sha256'
        $consoleSavedLock = if (Test-Path -LiteralPath $consoleLockRecord) { (Get-Content -LiteralPath $consoleLockRecord -Raw).Trim() } else { '' }
        if (-not (Test-Path -LiteralPath 'web/node_modules') -or $consoleLockHash -ne $consoleSavedLock) {
            Invoke-ConsoleCommand -Executable 'npm.cmd' -CommandArgs @('--prefix', 'web', 'ci')
            Set-Content -LiteralPath $consoleLockRecord -Value $consoleLockHash -Encoding ascii
        }
    }
    Invoke-ConsoleCommand -Executable 'npm.cmd' -CommandArgs @('--prefix', 'web', 'run', 'build')

    if ($Database -eq 'Postgres') {
        Invoke-ConsoleCommand -Executable 'docker' -CommandArgs @('info', '--format', '{{.ServerVersion}}')
        Invoke-ConsoleCommand -Executable $consolePython -CommandArgs @('scripts/configure_console.py', '--port', "$DatabasePort")
        $consoleDbFile = Join-Path $consoleRuntime 'postgres.env'
        $consoleDbSettings = @{}
        foreach ($consoleLine in Get-Content -LiteralPath $consoleDbFile) {
            if ($consoleLine -match '^([A-Z_]+)=(.*)$') { $consoleDbSettings[$Matches[1]] = $Matches[2] }
        }
        $consoleProject = 'aethertrade-console-' + $consoleDbSettings['AETHERTRADE_INSTANCE']
        Invoke-ConsoleCommand -Executable 'docker' -CommandArgs @('compose', '-p', $consoleProject, '--env-file', $consoleDbFile, '-f', 'deploy/console.compose.yaml', 'up', '-d', '--wait', '--wait-timeout', '60', 'postgres')
        $env:AETHERTRADE_DATABASE_URL = $consoleDbSettings['AETHERTRADE_DATABASE_URL']
    } else {
        $consoleDatabasePath = (Join-Path $consoleRuntime 'aethertrade.sqlite3').Replace('\','/')
        $env:AETHERTRADE_DATABASE_URL = 'sqlite:///' + $consoleDatabasePath
    }
    $env:AETHERTRADE_PORT = "$Port"
    if ($NoMarket) { $env:AETHERTRADE_MARKET_ENABLED = 'false' }
    Invoke-ConsoleCommand -Executable $consolePython -CommandArgs @('-m', 'trading_system.server.cli', 'init-db')
    if ($Foreground) {
        Write-Output "AetherTrade PAPER ($Database): $consoleUrl ; Ctrl+C to stop"
        Invoke-ConsoleCommand -Executable $consolePython -CommandArgs @('-m', 'trading_system.server.cli', 'serve')
        return
    }
    $consoleChild = Start-Process -FilePath $consolePython -ArgumentList @('-m', 'trading_system.server.cli', 'serve') -WorkingDirectory $consoleRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $consoleRuntime "api-$Port.stdout.log") -RedirectStandardError (Join-Path $consoleRuntime "api-$Port.stderr.log")
    $consoleState = [ordered]@{ pid = $consoleChild.Id; started_at = $consoleChild.StartTime.ToUniversalTime().ToString('o'); root = $consoleRoot; url = $consoleUrl; database = $Database }
    $consoleState | ConvertTo-Json | Set-Content -LiteralPath $consolePidFile -Encoding utf8
    $consoleReady = $false
    for ($consoleAttempt = 0; $consoleAttempt -lt 40; $consoleAttempt++) {
        $consoleChild.Refresh()
        if ($consoleChild.HasExited) { throw "API exited. Inspect .runtime/api-$Port.stderr.log" }
        try {
            $consoleHealth = Invoke-RestMethod -Uri "$consoleUrl/api/health" -TimeoutSec 2
            if ($consoleHealth.environment -eq 'PAPER') { $consoleReady = $true; break }
        } catch { Start-Sleep -Milliseconds 400 }
    }
    if (-not $consoleReady) { throw "API readiness timed out. Inspect .runtime/api-$Port.stderr.log" }
    Write-Output "AetherTrade PAPER ($Database) is ready: $consoleUrl"
    Write-Output 'First visit: create your local user and virtual account.'
    Write-Output "Stop: .\scripts\stop-console.ps1 -Port $Port"
    if (-not $NoBrowser) { Start-Process $consoleUrl }
} finally {
    # PowerShell preserves null here; .NET method binding can coerce it to an
    # empty string on recent runtimes, breaking subsequent Settings parsing.
    $env:AETHERTRADE_DATABASE_URL = $consolePreviousDatabase
    $env:AETHERTRADE_PORT = $consolePreviousPort
    $env:AETHERTRADE_MARKET_ENABLED = $consolePreviousMarket
    Pop-Location
}
