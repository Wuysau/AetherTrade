"""Fetch/verify only the explicitly pinned public source archive."""
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

COMMIT='27a8e54e7ac3c57d6cbf8891f0283dfbaee97317'
SHA256='3953eefd58bcfded576d6802d847fa8caf7a4bb7d5def362c8bbb887a162006e'
path=Path('.runtime/sdk-final-send/source.tar.gz')
path.parent.mkdir(parents=True,exist_ok=True)
if path.exists():data=path.read_bytes()
else:
    with urlopen(f'https://codeload.github.com/nautechsystems/nautilus_trader/tar.gz/{COMMIT}',timeout=120) as response:
        data=response.read()
assert sha256(data).hexdigest()==SHA256,'PINNED_SOURCE_ARCHIVE_CHANGED'
if not path.exists():path.write_bytes(data)
print({'source_commit':COMMIT,'archive_sha256':SHA256,'bytes':len(data)})
