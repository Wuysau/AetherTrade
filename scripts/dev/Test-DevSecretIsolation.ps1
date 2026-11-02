# Run under aether-dev-runner. Opens a read handle but never reads secret bytes.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.Name -ine ($env:COMPUTERNAME + '\aether-dev-runner')) {
    throw 'Wrong identity: this check must run as aether-dev-runner.'
}
$status = 'BLOCKED'
$reason = 'NOT_RUN'
$handle = $null
try {
    $handle = [IO.File]::Open('D:\Projects\Python\AetherTrade\.secrets\dev_admin_password',
        [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    $status = 'FAIL'
    $reason = 'SECRET_READ_HANDLE_ALLOWED'
} catch {
    $cause = $_.Exception.GetBaseException()
    if ($cause -is [UnauthorizedAccessException]) {
        $status = 'PASS'
        $reason = 'SECRET_READ_HANDLE_DENIED'
    } else {
        $reason = $cause.GetType().FullName
    }
} finally {
    if ($null -ne $handle) { $handle.Dispose() }
}
@{status=$status; reason=$reason; identity=$identity.Name;
    recorded_at=[DateTime]::UtcNow.ToString('o'); scope='DEV admin secret read access only'} |
    ConvertTo-Json
if ($status -eq 'PASS') { exit 0 }
if ($status -eq 'FAIL') { exit 1 }
exit 78
