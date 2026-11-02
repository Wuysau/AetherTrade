# Host-admin DEV initialization. The Agent must not run this.
# It starts only the fixed DEV PostgreSQL service, then stops for migration review.
param(
    [string]$EnvFile = "deploy/dev.env.local",
    [string]$ComposeFile = "deploy/dev.compose.yaml"
)

$ErrorActionPreference = "Stop"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

Require-Command "docker"
if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Copy deploy/dev.env.example and set the pinned image and secret path."
}
if (-not (Test-Path $ComposeFile)) {
    throw "Missing $ComposeFile"
}

docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is unavailable; start Docker Desktop/Engine first."
}

docker compose --env-file $EnvFile -f $ComposeFile config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Compose configuration is invalid."
}

docker compose --env-file $EnvFile -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) {
    throw "DEV compose startup failed."
}

$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 2
    $health = docker inspect --format '{{.State.Health.Status}}' aether-dev-postgres 2>$null
    if ($LASTEXITCODE -ne 0) { throw "DEV container is missing." }
} while ($health -ne "healthy" -and (Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    throw "DEV PostgreSQL did not become healthy within 3 minutes."
}

$id = docker inspect --format '{{.Id}}' aether-dev-postgres
$image = docker inspect --format '{{.Config.Image}}' aether-dev-postgres
$port = docker port aether-dev-postgres 5432/tcp
if (-not $port) {
    throw "DEV PostgreSQL is healthy inside the container but has no published host port. Recreate the project after checking deploy/dev.compose.yaml networks/ports."
}
$volume = docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' aether-dev-postgres
$network = docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' aether-dev-postgres

Write-Host "DEV PostgreSQL is healthy."
Write-Host "container_id=$id"
Write-Host "image=$image"
Write-Host "port=$port"
Write-Host "volume=$volume"
Write-Host "network=$network"
Write-Host ""
Write-Host "Next: review and run the controlled migration steps in agent-output/plan/OP-G-ENVIRONMENT-CREATION-PLAN.md section 7."
Write-Host "Do not apply 0030/0031 to a shared database before the disposable at_dev_* cohort passes."
