# Administrator only. Changes the ACL of exactly one existing secret file.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated administrator PowerShell.'
}
$secretPath = 'D:\Projects\Python\AetherTrade\.secrets\dev_admin_password'
$runner = Get-LocalUser -Name 'aether-dev-runner'
$item = Get-Item -LiteralPath $secretPath -Force
if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Expected an ordinary existing secret file.'
}
$acl = Get-Acl -LiteralPath $secretPath
$backup = Join-Path 'D:\AetherTradeRunner\control' ('secret-acl-before-' + [guid]::NewGuid().ToString('N') + '.json')
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
# Does not change any parent directory, Docker service, database or TEST resource.
