"""Verify compressed evidence bytes against every published index; no services used."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent
archives = 0
members = 0
for index_path in sorted(root.glob('*-index.json')):
    index = json.loads(index_path.read_text())
    archive = root / index.get('raw_archive', index.get('archive', ''))
    assert archive.is_file(), index_path.name
    digest = index.get('raw_archive_sha256', index.get('archive_sha256'))
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest, archive.name
    expected = index.get('raw_files_sha256', index.get('files_sha256'))
    with zipfile.ZipFile(archive) as source:
        assert source.testzip() is None, archive.name
        assert set(source.namelist()) == set(expected), archive.name
        for name, checksum in expected.items():
            assert hashlib.sha256(source.read(name)).hexdigest() == checksum, name
        if 'launcher-manifest.json' in source.namelist():
            launcher = json.loads(source.read('launcher-manifest.json'))
            for name, checksum in launcher['files_sha256'].items():
                assert checksum == expected[name], name
    archives += 1
    members += len(expected)
scope_path = root / 'final-source-scope.json'
if scope_path.exists():
    scope = json.loads(scope_path.read_text())
    backend = json.loads((root / scope['backend_gate_index']).read_text())
    frontend = json.loads((root / scope['frontend_check_index']).read_text())
    assert backend['source_commit'] == scope['backend_gate_source_commit']
    if 'backend_identity_index' in scope:
        identity = json.loads((root / scope['backend_identity_index']).read_text())['backend_identity_verified']
        assert identity['all_matching']
        assert identity['qualified_source'] == backend['source_commit']
        assert identity['current_source'] == scope['current_source_commit']
    assert frontend['source_commit'] == scope['frontend_check_source_commit']
    assert len(set(scope['frontend_tree_identity_verified'].values())) == 1
print(f'Verified {archives} archives / {members} original files; all SHA256 checks match.')
