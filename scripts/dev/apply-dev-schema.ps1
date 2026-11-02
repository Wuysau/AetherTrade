# Host-admin controlled schema bootstrap for the fixed DEV PostgreSQL.
# The Agent must not run this. It refuses to reapply over existing schema.
#
# Topology (all in the one fixed DEV cluster):
#   ledger  : paper_runtime_dev   (ledger.* business ledger)
#   venue   : paper_engine_dev    (public.paper_* synthetic venue; name is required)
#   console : paper_control_dev   (console/control database, no ledger migrations)
param(
    [string]$Container = "aether-dev-postgres",
    [string]$AdminUser = "aether_dev_admin",
    [string]$LedgerDatabase = "paper_runtime_dev",
    [string]$VenueDatabase = "paper_engine_dev",
    [string]$ConsoleDatabase = "paper_control_dev",
    [switch]$CreateDatabases,
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

    foreach ($name in @($LedgerDatabase, $VenueDatabase, $ConsoleDatabase)) {
        if ($name -notmatch '^[a-z_][a-z0-9_]*$') { throw "Invalid database name: $name" }
    }
    if (-not $VenueDatabase.StartsWith("paper_engine_")) {
        throw "Venue database must start with paper_engine_."
    }

    function Invoke-Psql([string]$Database, [string]$Sql) {
        $Sql | docker exec -i $Container psql -U $AdminUser -d $Database -v ON_ERROR_STOP=1 -f -
        if ($LASTEXITCODE -ne 0) { throw "SQL failed in $Database" }
    }

    function Ensure-Database([string]$Database) {
        $exists = docker exec $Container psql -U $AdminUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$Database';" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect cluster databases." }
        if ([string]::IsNullOrWhiteSpace([string]$exists)) {
            if (-not $CreateDatabases) {
                throw "Database $Database is missing. Re-run with -CreateDatabases or create it explicitly."
            }
            docker exec $Container psql -U $AdminUser -d postgres -c "CREATE DATABASE $Database OWNER $AdminUser;" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Cannot create database $Database" }
        }
    }

    function Apply-Sql([string]$Database, [string]$RelativePath) {
        $path = Join-Path $Root $RelativePath
        if (-not (Test-Path $path)) { throw "Missing SQL file: $RelativePath" }
        Write-Host "[$Database] Applying $RelativePath"
        Get-Content -Raw $path | docker exec -i $Container psql -U $AdminUser -d $Database -v ON_ERROR_STOP=1 -f -
        if ($LASTEXITCODE -ne 0) { throw "Migration failed in $Database : $RelativePath" }
    }

    Ensure-Database $LedgerDatabase
    Ensure-Database $VenueDatabase
    Ensure-Database $ConsoleDatabase

    $ledgerExists = docker exec $Container psql -U $AdminUser -d $LedgerDatabase -tAc "SELECT to_regnamespace('ledger');" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect ledger database." }
    if (-not [string]::IsNullOrWhiteSpace([string]$ledgerExists)) { throw "ledger schema already exists in $LedgerDatabase; refusing to reapply." }

    $venueExists = docker exec $Container psql -U $AdminUser -d $VenueDatabase -tAc "SELECT to_regclass('public.paper_engine_orders');" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect venue database." }
    if (-not [string]::IsNullOrWhiteSpace([string]$venueExists)) { throw "paper_engine_orders already exists in $VenueDatabase; refusing to reapply." }

    Apply-Sql $LedgerDatabase "migrations/0001_business_ledger.sql"
    Apply-Sql $LedgerDatabase "deploy/db/roles.sql"

    $ledgerMigrations = @(
        "0002_risk_resolution_authority.sql",
        "0004_command_abort_guard.sql",
        "0005_owner_cas_authority.sql",
        "0006_proposal_intake_role.sql",
        "0007_owner_controller_ddl_guard.sql",
        "0008_order_program_records.sql",
        "0009_order_program_closure.sql",
        "0010_order_program_intake.sql",
        "0011_order_program_financial_credit_guard.sql",
        "0012_program_command_boundary.sql",
        "0015_quote_program_dispatch.sql",
        "0016_program_fill_source_approval.sql",
        "0017_program_fill_source_snapshots.sql",
        "0018_program_fill_source_leaves.sql",
        "0019_program_account_opening.sql",
        "0020_program_legacy_opening.sql",
        "0021_program_submission_observations.sql",
        "0022_program_management_boundary.sql",
        "0024_program_opening_exclusivity.sql",
        "0027_effective_order_management.sql",
        "0029_effective_management_source.sql"
    )
    foreach ($name in $ledgerMigrations) { Apply-Sql $LedgerDatabase ("migrations/" + $name) }

    $venueMigrations = @(
        "0003_paper_engine.sql",
        "0013_paper_market_matcher.sql",
        "0014_paper_market_matcher_v2.sql",
        "0023_paper_management.sql",
        "0025_paper_quote_budget.sql",
        "0026_paper_native_execution.sql",
        "0028_paper_effective_orders.sql"
    )
    foreach ($name in $venueMigrations) { Apply-Sql $VenueDatabase ("migrations/" + $name) }

    if ($IncludeLocalChildCandidates) {
        Apply-Sql $LedgerDatabase "migrations/0030_local_child_commitments.sql"
        Apply-Sql $LedgerDatabase "migrations/0031_local_child_admission.sql"
    }

    Write-Host "Schema bootstrap complete."
    Write-Host "Ledger=$LedgerDatabase Venue=$VenueDatabase Console=$ConsoleDatabase"
    Write-Host "0030/0031 were skipped unless -IncludeLocalChildCandidates was set."
    Write-Host "Validate runtime roles and a disposable at_dev_* database before OP-G."
}
finally {
    Pop-Location
}
