"""Temporary-file checks for owner bootstrap credential handling."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.owner_credentials import CredentialError, OwnerCredentialStore  # noqa: E402


class OwnerCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="onepath-owner-credentials-")
        self.store = OwnerCredentialStore(Path(self.temp.name))

    def tearDown(self): self.temp.cleanup()

    def test_create_read_rotate_and_secret_not_in_exception(self):
        created = self.store.create("http://127.0.0.1:8777")
        self.assertEqual(created, self.store.read())
        self.assertEqual(len(__import__("base64").urlsafe_b64decode(created.bootstrap_secret + "==")), 32)
        rotated = self.store.rotate()
        self.assertNotEqual(created.instance_id, rotated.instance_id)
        self.assertNotEqual(created.bootstrap_secret, rotated.bootstrap_secret)
        self.assertEqual(rotated, self.store.read())

    def test_origin_must_be_literal_loopback_root_without_components(self):
        for origin in ("http://localhost:80", "https://example.com", "http://127.0.0.1/path", "http://127.0.0.1/?x=1", "http://user@127.0.0.1", "http://[::2]"):
            with self.subTest(origin=origin), self.assertRaises(CredentialError) as caught:
                self.store.create(origin)
            self.assertEqual(caught.exception.code, "INVALID_ORIGIN")
            self.assertNotIn(origin, str(caught.exception))

    def test_existing_file_is_not_overwritten_and_posix_modes_are_private(self):
        self.store.directory.mkdir(mode=0o700)
        self.store.path.write_text("owner sentinel")
        with self.assertRaises(CredentialError): self.store.create("http://127.0.0.1")
        self.assertEqual(self.store.path.read_text(), "owner sentinel")

    def test_malformed_record_is_rejected_with_fixed_error(self):
        self.store.create("http://127.0.0.1")
        self.store.path.write_text('{"version":1,"bootstrap_secret":"not-a-secret"}')
        with self.assertRaises(CredentialError) as caught: self.store.read()
        self.assertEqual(caught.exception.code, "CREDENTIAL_INVALID")
        self.assertNotIn("not-a-secret", str(caught.exception))

    @unittest.skipIf(os.name == "nt", "POSIX mode proof applies only on POSIX")
    def test_posix_file_and_directory_modes_and_hostile_mode_rejected(self):
        record = self.store.create("http://127.0.0.1")
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.store.path.chmod(0o644)
        with self.assertRaises(CredentialError): self.store.read()

    @unittest.skipUnless(os.name == "nt", "Native Windows ACL proof requires Windows")
    def test_windows_acl_open_handle_validation(self):
        self.store.create("http://127.0.0.1")
        self.assertEqual(self.store.read(), self.store.read())

    @unittest.skipUnless(os.name == "nt", "Native Windows ACL proof requires Windows")
    def test_windows_inherited_acl_is_rejected(self):
        self.store._create_private_directory()
        self.store.path.write_bytes(b'{"version":1}')
        with self.assertRaises(CredentialError): self.store.read()

    @unittest.skipUnless(os.name == "nt", "Native Windows ACL proof requires Windows")
    def test_windows_junction_is_rejected_before_read(self):
        root = Path(self.temp.name).resolve(strict=True)
        link = root / OwnerCredentialStore.DIRECTORY
        target = root / "junction-target"
        target.mkdir()
        self.assertTrue(target.resolve(strict=True).is_relative_to(root))
        self.assertTrue(link.parent.resolve(strict=True).is_relative_to(root))
        (target / OwnerCredentialStore.FILENAME).write_bytes(b"DO_NOT_READ_SENTINEL")
        ps = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(target).replace("'", "''") + "' | Out-Null"
        try:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, capture_output=True, timeout=15)
            self.assertTrue(link.is_junction(), "PowerShell did not create a junction")
            with self.assertRaises(CredentialError): self.store.read()
        finally:
            if link.is_junction():
                os.rmdir(link)  # Remove only the junction entry; never recurse through it.


if __name__ == "__main__": unittest.main()
