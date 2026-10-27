param(
    [string]$Label = 'group-daily',
    [string]$Runner = 'aethertrade-pg-ledger-pgledger_7fa85478',
    [string]$RuntimeDirectory = 'D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution/.runtime',
    [string[]]$TestPaths = @('tests/integration/test_group_daily_locks.py')
)
$ErrorActionPreference = 'Stop'
if ($Label -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid evidence label' }
$repository = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repository
$runtime = Get-Content -LiteralPath (Join-Path $RuntimeDirectory 'paper-postgres.json') -Raw | ConvertFrom-Json
$settings = @{}
Get-Content -LiteralPath (Join-Path $RuntimeDirectory 'paper-postgres.env') | ForEach-Object {
    $pair = $_ -split '=', 2
    if ($pair.Count -eq 2) { $settings[$pair[0]] = $pair[1] }
}
$database = 'ip08_test_group_' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$runDirectory = '/tmp/' + $database
$evidenceDirectory = Join-Path $repository '.runtime/group-daily'
New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null
$prefix = Join-Path $evidenceDirectory ($Label + '-' + $database)
docker exec $runtime.container psql -U $settings.POSTGRES_USER -d $runtime.database -v ON_ERROR_STOP=1 -c "CREATE DATABASE $database"
if ($LASTEXITCODE -ne 0) { throw 'Disposable database creation failed' }
$dsn = 'postgresql://' + [uri]::EscapeDataString($settings.POSTGRES_USER) + ':' + [uri]::EscapeDataString($settings.POSTGRES_PASSWORD) + '@' + $runtime.container + ':5432/' + $database
[System.IO.File]::WriteAllText($prefix + '.env', "IP08_ISOLATED_DATABASE=YES`nIP08_DATABASE_DSN=$dsn`nPYTHONPATH=$runDirectory/src`:$runDirectory/tests`:$runDirectory`n")
$sourcePaths = @('src/trading_system/storage/uow.py', 'src/trading_system/risk/locks.py',
    'src/trading_system/risk/locked_actions.py', 'src/trading_system/risk/reservations.py',
    'tests/integration/test_group_daily_locks.py', 'tests/unit/test_risk_applicability.py',
    'migrations/0001_business_ledger.sql', 'migrations/0002_risk_resolution_authority.sql')
$sourceHashes = foreach ($sourcePath in $sourcePaths) {
    @{path=$sourcePath; sha256=(Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()}
}
@{database=$database; runner=$Runner; directory=$runDirectory; postgres_container=$runtime.container;
    source_commit=(& git rev-parse HEAD); source_hashes=$sourceHashes; tests=$TestPaths} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath ($prefix + '.json')
docker exec $Runner mkdir -p $runDirectory
if ($LASTEXITCODE -ne 0) { throw 'Linux runner unavailable' }
foreach ($directory in @('src', 'tests', 'migrations', 'deploy')) {
    docker cp $directory ($Runner + ':' + $runDirectory + '/')
    if ($LASTEXITCODE -ne 0) { throw 'Source copy failed' }
}
docker cp pyproject.toml ($Runner + ':' + $runDirectory + '/')
if ($LASTEXITCODE -ne 0) { throw 'Project configuration copy failed' }
$platformOutput = & docker exec --env-file ($prefix + '.env') --workdir $runDirectory $Runner python -c "import os,sys,platform,json,psycopg; c=psycopg.connect(os.environ['IP08_DATABASE_DSN']); print(json.dumps({'platform':sys.platform,'python':platform.python_version(),'postgresql':c.execute('SHOW server_version').fetchone()[0],'database':c.execute('SELECT current_database()').fetchone()[0]})); c.close()" 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Linux/PostgreSQL environment probe failed' }
$testOutput = & docker exec --env-file ($prefix + '.env') --workdir $runDirectory $Runner python -m pytest @TestPaths -q --tb=short -p no:cacheprovider 2>&1
$testExit = $LASTEXITCODE
# Preserve the tool output except test-only secrets, including failure tracebacks.
$sanitized = (@($platformOutput) + @($testOutput) | Out-String).Replace($settings.POSTGRES_PASSWORD, '[REDACTED]').Replace([uri]::EscapeDataString($settings.POSTGRES_PASSWORD), '[REDACTED]')
Set-Content -LiteralPath ($prefix + '.log') -Value $sanitized
Set-Content -LiteralPath ($prefix + '.exit') -Value $testExit
Write-Output $sanitized
Write-Output "GROUP_DAILY_EXIT=$testExit DATABASE=$database LOG=$prefix.log"
exit $testExit
