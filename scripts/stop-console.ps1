param([ValidateRange(1,65535)][int]$Port = 8787, [switch]$StopDatabase)
$ErrorActionPreference = 'Stop'
$consoleRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$consoleRuntime = Join-Path $consoleRoot '.runtime'
$consolePidFile = Join-Path $consoleRuntime "server-$Port.json"
if (Test-Path -LiteralPath $consolePidFile) {
    $consoleState = Get-Content -LiteralPath $consolePidFile -Raw | ConvertFrom-Json
    if ($consoleState.root -ne $consoleRoot) { throw 'Process record belongs to another checkout' }
    $consoleProcess = Get-Process -Id $consoleState.pid -ErrorAction SilentlyContinue
    if ($consoleProcess -and $consoleProcess.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$consoleState.started_at).UtcDateTime.Ticks) {
        $consoleCommand = Get-CimInstance Win32_Process -Filter "ProcessId=$($consoleState.pid)"
        if ($consoleCommand.CommandLine -notlike '*trading_system.server.cli*') { throw 'PID command does not match AetherTrade; no process stopped' }
        Stop-Process -Id $consoleState.pid
        Write-Output "Stopped AetherTrade API on port $Port. Database data is retained."
    } else { Write-Output 'AetherTrade API is already stopped.' }
} else { Write-Output 'No AetherTrade process record for this port.' }
if ($StopDatabase) {
    $consoleDbFile = Join-Path $consoleRuntime 'postgres.env'
    if (-not (Test-Path -LiteralPath $consoleDbFile)) { throw 'No local PostgreSQL settings for this checkout' }
    $consoleInstance = (Get-Content -LiteralPath $consoleDbFile | Where-Object { $_ -like 'AETHERTRADE_INSTANCE=*' }) -replace '^AETHERTRADE_INSTANCE=', ''
    if ($consoleInstance -notmatch '^[0-9a-f]{10}$') { throw 'Invalid local database instance' }
    & docker compose -p "aethertrade-console-$consoleInstance" --env-file $consoleDbFile -f (Join-Path $consoleRoot 'deploy/console.compose.yaml') stop postgres
    if ($LASTEXITCODE -ne 0) { throw 'Database stop failed' }
    Write-Output 'PostgreSQL stopped; its persistent volume is retained.'
}
