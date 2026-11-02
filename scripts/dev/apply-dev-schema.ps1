# Host-admin controlled schema bootstrap for the fixed DEV PostgreSQL.
# The Agent must not run this. It refuses to reapply over an existing ledger schema.
param(
    [string]$Container = "aether-dev-postgres",
    [string]$Database = "aether_dev",
    [string]$User = "aether_dev_admin",
    [switch]$IncludeLocalChildCandidates
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Push-Location $Root
try {
    $health = docker inspect --format '{{.State.Health.Status}}' $Container 2>$null
    if ($LASTEXITCODE -ne 0 -or $health -ne "healthy") {
        throw "Container $Container is missing or not healthy."
    }

    $existing = docker exec $Container psql -U $User -d $Database -tAc "SELECT to_regnamespace('ledger');" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot inspect database $Database."
    }
    if ($existing.Trim()) {
        throw "ledger schema already exists; refusing to reapply migrations."
    }

    function Apply-Sql([string]$RelativePath) {
        $path = Join-Path $Root $RelativePath
        if (-not (Test-Path $path)) { throw "Missing SQL file: $RelativePath" }
        Write-Host "Applying $RelativePath"
        Get-Content -Raw $path | docker exec -i $Container psql -U $User -d $Database -v ON_ERROR_STOP=1 -f -
        if ($LASTEXITCODE -ne 0) { throw "Migration failed: $RelativePath" }
    }

    Apply-Sql "migrations/0001_business_ledger.sql"
    Apply-Sql "deploy/db/roles.sql"

    $migrations = Get-ChildItem (Join-Path $Root "migrations") -Filter "*.sql" |
        Where-Object {
            $_.Name -match '^00\d\d_' -and
            $_.Name -notmatch '^0001_' -and
            $_.Name -notmatch '^0030_' -and
            $_.Name -notmatch '^0031_'
        } |
        Sort-Object Name

    foreach ($migration in $migrations) {
        Apply-Sql ("migrations/" + $migration.Name)
    }

    if ($IncludeLocalChildCandidates) {
        Apply-Sql "migrations/0030_local_child_commitments.sql"
        Apply-Sql "migrations/0031_local_child_admission.sql"
    }

    Write-Host "Schema bootstrap complete."
    Write-Host "Applied through 0029. 0030/0031 were skipped unless -IncludeLocalChildCandidates was set."
    Write-Host "Validate runtime roles and a disposable at_dev_* database before OP-G."
}
finally {
    Pop-Location
}
