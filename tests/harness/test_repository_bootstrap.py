import json
import os
from pathlib import Path
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/repository/bootstrap_public_repo.ps1'
def invoke(target, seed=ROOT):
    return subprocess.run(['pwsh','-NoProfile','-File',str(SCRIPT),'-Owner','Wuysau','-RepoName','AetherTrade','-LocalPath',str(target),'-SeedPath',str(seed)], capture_output=True, text=True, encoding="utf-8")
def test_publication_preflight_rejects_nonempty_existing_repo(tmp_path):
    for name in ('.git', 'user.txt'):
        target = tmp_path / name.replace('.', '_')
        target.mkdir()
        (target / name).write_text('preserve')
        result = invoke(target)
        assert result.returncode != 0
        assert 'Target is not empty' in result.stderr
        assert (target / name).read_text() == 'preserve'
def test_publication_dry_run_has_no_side_effect(tmp_path):
    target = tmp_path / 'absent'
    result = invoke(target)
    assert result.returncode == 0, result.stderr
    assert 'DRY RUN' in result.stdout
    assert not target.exists()
def test_staged_files_allowlist_and_secret_block(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    (seed / '.env').write_text('private')
    result = invoke(tmp_path / 'target', seed)
    assert result.returncode != 0 and 'Disallowed file' in result.stderr
    (seed / '.env').unlink()
    (seed / 'README.md').write_text('gh' + 'p_' + 'a' * 36)
    result = invoke(tmp_path / 'target', seed)
    assert result.returncode != 0 and 'Potential secret' in result.stderr
    assert not (tmp_path / 'target').exists()
def test_real_github_repo_and_branches():
    if os.environ.get('IP34_VERIFY_REMOTE') != '1':
        pytest.skip('BLOCKED: real remote not yet created/verification not requested')
    meta = json.loads(subprocess.check_output(['gh','repo','view','Wuysau/AetherTrade','--json','nameWithOwner,visibility,defaultBranchRef'], text=True))
    assert meta['nameWithOwner'] == 'Wuysau/AetherTrade'
    assert meta['visibility'] == 'PUBLIC'
    assert meta['defaultBranchRef']['name'] == 'main'
    origin = subprocess.check_output(['git','remote','get-url','origin'],cwd=ROOT,text=True).strip()
    assert origin == 'https://github.com/Wuysau/AetherTrade.git'
    heads = subprocess.check_output(['git','ls-remote','origin','refs/heads/main','refs/heads/dev'],cwd=ROOT,text=True)
    local = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    assert {line.split()[1] for line in heads.splitlines()} == {'refs/heads/main','refs/heads/dev'}
    assert all(line.split()[0] == local for line in heads.splitlines())
