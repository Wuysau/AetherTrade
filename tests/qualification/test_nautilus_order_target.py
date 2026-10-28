"""Manifest guard tests; these are not exchange/native-order qualification."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'qualification/nautilus/order_target'
spec = importlib.util.spec_from_file_location('order_target_probe', TARGET / 'probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TargetLockTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((TARGET / 'manifest.json').read_text(encoding='utf-8'))
        self.lock = (TARGET / 'requirements-win-cp314.lock').read_text(encoding='utf-8')

    def test_manifest_lock_agree(self):
        probe.validate_manifest(self.manifest, self.lock)

    def test_no_external_mode_enablement(self):
        for mode in ('TESTNET', 'DEMO', 'LIVE'):
            changed = copy.deepcopy(self.manifest)
            changed['operating_modes'][mode] = True
            with self.assertRaises(ValueError):
                probe.validate_manifest(changed, self.lock)

    def test_hash_and_abi_changes_rejected(self):
        with self.assertRaises(ValueError):
            probe.validate_manifest(self.manifest, self.lock.replace('--hash=sha256:', '--hash=sha512:', 1))
        self.manifest['target']['abi'] = 'cp313'
        with self.assertRaises(ValueError):
            probe.validate_manifest(self.manifest, self.lock)

    def test_inspection_cannot_grant_qualification(self):
        self.manifest['native_order_qualification'] = 'PASS'
        with self.assertRaises(ValueError):
            probe.validate_manifest(self.manifest, self.lock)

    def test_legacy_reference_preserved(self):
        import hashlib
        actual = hashlib.sha256((ROOT / 'qualification/nautilus/requirements.lock').read_bytes()).hexdigest()
        self.assertEqual(actual, self.manifest['legacy_lock_sha256'])


if __name__ == '__main__':
    unittest.main()
