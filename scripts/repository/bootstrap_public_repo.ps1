[CmdletBinding()]
param([string]$Owner='Wuysau',[string]$RepoName='AetherTrade',
[Parameter(Mandatory)][string]$LocalPath,[Parameter(Mandatory)][string]$SeedPath,[switch]$Execute)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Invoke-PublicRepoBootstrap {
    param([string]$Owner,[string]$RepoName,[string]$LocalPath,[string]$SeedPath,[switch]$Execute)
    function Native([string]$Name,[string[]]$Arguments) {
        & $Name @Arguments
        if($LASTEXITCODE -ne 0){throw "${Name} failed: $LASTEXITCODE"}
    }
    $target=[IO.Path]::GetFullPath($LocalPath)
    if(Test-Path -LiteralPath $target){
        if(!(Test-Path -LiteralPath $target -PathType Container) -or @(Get-ChildItem -LiteralPath $target -Force).Count){throw 'Target is not empty'}
    }
    $allowed=@('README.md','LICENSE','SECURITY.md','CONTRIBUTING.md','CODE_OF_CONDUCT.md','.gitignore','.github/ISSUE_TEMPLATE/bug_report.yml','.github/pull_request_template.md','docs/governance/PUBLICATION_POLICY.md','scripts/repository/bootstrap_public_repo.ps1','tests/harness/test_repository_bootstrap.py','docs/worklog/IP-34.md')
    $files=@(Get-ChildItem -LiteralPath $SeedPath -Recurse -File -Force | Where-Object { $_.FullName -notmatch '[\\/](\.git|__pycache__|\.pytest_cache)[\\/]' })
    foreach($file in $files){
        if($file.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Links forbidden'}
        $rel=[IO.Path]::GetRelativePath($SeedPath,$file.FullName).Replace('\','/')
        if($rel -notin $allowed -and $rel -notmatch '^evidence/tasks/IP-34/[A-Za-z0-9_.-]+\.(txt|json|md)$'){throw "Disallowed file: $rel"}
        $raw=[IO.File]::ReadAllText($file.FullName)
        if($raw -match '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|(?im)^\s*(api[_-]?key|api[_-]?secret|password|access[_-]?token)\s*[:=]\s*\S+'){throw "Potential secret: $rel"}
        Write-Output $rel
    }
    Write-Output "PUBLIC https://github.com/$Owner/$RepoName -> $target"
    if(!$Execute){Write-Output 'DRY RUN: no local or remote changes'; return}
    if($Owner -notmatch '^[A-Za-z0-9-]+$' -or $RepoName -notmatch '^[A-Za-z0-9_.-]+$'){throw 'Invalid repository name'}
    $login=(& gh api user --jq .login)
    if($LASTEXITCODE -ne 0 -or $login -cne $Owner){throw 'Authentication/owner mismatch'}
    $repos=(& gh api "repos/$Owner/$RepoName" 2>&1 | Out-String)
    if($LASTEXITCODE -eq 0 -or $repos -notmatch '404'){throw 'Remote exists or absence could not be established'}
    foreach($required in $allowed){if(!(Test-Path -LiteralPath (Join-Path $SeedPath $required))){throw "Missing $required"}}
    if(!(Test-Path -LiteralPath $target)){New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null}
    foreach($file in $files){
        $dest=Join-Path $target ([IO.Path]::GetRelativePath($SeedPath,$file.FullName))
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        if(Test-Path -LiteralPath $dest){throw 'Refusing overwrite'}
        Copy-Item -LiteralPath $file.FullName -Destination $dest
    }
    Native git @('-C',$target,'init','-b','main')
    Native git @('-C',$target,'add','--','.')
    Native git @('-C',$target,'diff','--cached','--check')
    Native git @('-C',$target,'commit','-m','feat(ip-34): 创建 GitHub 公开仓库与开源治理基线')
    Native gh @('repo','create',"$Owner/$RepoName",'--public','--source',$target,'--remote','origin','--push')
    Native git @('-C',$target,'branch','dev')
    Native git @('-C',$target,'push','-u','origin','dev')
    Native gh @('repo','view',"$Owner/$RepoName",'--json','nameWithOwner,visibility,defaultBranchRef')
    Native git @('-C',$target,'ls-remote','origin','refs/heads/main','refs/heads/dev')
}
Invoke-PublicRepoBootstrap -Owner $Owner -RepoName $RepoName -LocalPath $LocalPath -SeedPath $SeedPath -Execute:$Execute
