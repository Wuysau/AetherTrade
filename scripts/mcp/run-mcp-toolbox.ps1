# Long-lived local MCP Toolbox for Databases process.
# Started by the per-user logon task; binds loopback only.
param(
    [string]$Exe = "C:\Users\Wuysa\AppData\Local\AetherTrade\mcp-toolbox\toolbox.exe",
    [string]$Config = "D:\Projects\Python\AetherTrade\AetherTrade\mcp\toolbox\tools.yaml",
    [string]$PasswordFile = "D:\Projects\Python\AetherTrade\.secrets\mcp_toolbox_db_password",
    [string]$LogFile = "D:\Projects\Python\AetherTrade\AetherTrade\agent-output\mcp-toolbox\toolbox.log"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) { throw "Missing toolbox.exe: $Exe" }
if (-not (Test-Path $Config)) { throw "Missing config: $Config" }
if (-not (Test-Path $PasswordFile)) { throw "Missing MCP database password file: $PasswordFile" }

New-Item -ItemType Directory -Force (Split-Path $LogFile -Parent) | Out-Null
$env:AETHER_MCP_DB_PASSWORD = (Get-Content $PasswordFile -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($env:AETHER_MCP_DB_PASSWORD)) {
    throw "MCP database password file is empty"
}

$existing = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    "MCP Toolbox port 5000 already listening; not starting a duplicate." | Out-File -Append -Encoding utf8 $LogFile
    exit 0
}

"Starting MCP Toolbox at $(Get-Date -Format o)" | Out-File -Append -Encoding utf8 $LogFile
while ($true) {
    & $Exe --config $Config --address 127.0.0.1 --port 5000 `
        --allowed-hosts 127.0.0.1,localhost `
        --allowed-origins http://127.0.0.1:5000,http://localhost:5000 `
        --disable-version-check *>> $LogFile
    "MCP Toolbox exited at $(Get-Date -Format o); restarting in 5 seconds." | Out-File -Append -Encoding utf8 $LogFile
    Start-Sleep -Seconds 5
}
