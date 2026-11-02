# Administrator only. Optionally repairs control directory administrator access;
# then changes the ACL of exactly one existing secret file.
[CmdletBinding()]
param([switch]$RepairControlAcl)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated administrator PowerShell.'
}
$controlPath = 'D:\AetherTradeRunner\control'
if ($RepairControlAcl) {
    # The original setup removed inherited administrator/SYSTEM grants when
    # protecting control. Restore those two identities explicitly, never runner.
    # Do not recurse, take ownership, reset ACLs or follow redirected directories.
    foreach ($directory in @('D:\AetherTradeRunner', $controlPath)) {
        $directoryItem = Get-Item -LiteralPath $directory -Force
        if (-not $directoryItem.PSIsContainer -or
            ($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Expected ordinary existing runner/control directories.'
        }
    }
    & "$env:SystemRoot\System32\icacls.exe" $controlPath /grant:r `
        '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
    if ($LASTEXITCODE -ne 0) { throw 'Control ACL repair failed; secret ACL unchanged.' }
    Write-Output 'CONTROL_ACL_UPDATED: explicit SYSTEM and Administrators access restored.'
}
$secretPath = 'D:\Projects\Python\AetherTrade\.secrets\dev_admin_password'
$runner = Get-LocalUser -Name 'aether-dev-runner'
$item = Get-Item -LiteralPath $secretPath -Force
if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Expected an ordinary existing secret file.'
}
$acl = Get-Acl -LiteralPath $secretPath
$backup = Join-Path $controlPath ('secret-acl-before-' + [guid]::NewGuid().ToString('N') + '.json')
@{path=$secretPath; sddl=$acl.Sddl; recorded_at=[DateTime]::UtcNow.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath $backup -Encoding UTF8
$rule = [Security.AccessControl.FileSystemAccessRule]::new(
    $runner.SID, [Security.AccessControl.FileSystemRights]::FullControl,
    [Security.AccessControl.AccessControlType]::Deny)
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $secretPath -AclObject $acl
$actual = Get-Acl -LiteralPath $secretPath
$denied = @($actual.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) |
    Where-Object { $_.IdentityReference.Value -eq $runner.SID.Value -and
        $_.AccessControlType -eq 'Deny' -and
        ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadData) })
if ($denied.Count -eq 0) { throw 'Deny rule was not persisted.' }
Write-Output 'ACL_UPDATED: runner denial configured; run Test-DevSecretIsolation.ps1 as runner.'
Write-Output ('ACL backup: ' + $backup)
# Does not change the runner root, Docker service, database or TEST resource.
