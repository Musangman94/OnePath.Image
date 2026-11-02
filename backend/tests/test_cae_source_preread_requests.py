from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from app.services.cae_source_capture_scope import SourceCaptureScopeV1
from app.services.cae_source_files import SourceReadLimits
from app.storage import cae_source_preread_requests as requests
from app.storage.cae_source_approvals import RootIdentity, initialize as init_approvals


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class SourcePrereadRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cae-preread-request-")
        self.connection = sqlite3.connect(Path(self.temp.name) / "requests.sqlite3", timeout=5)
        self.connection.row_factory = sqlite3.Row
        init_approvals(self.connection)
        requests.initialize(self.connection)
        self.scope = SourceCaptureScopeV1(
            schema_version=1,
            root_path=r"C:\synthetic\repo",
            root_identity=RootIdentity("windows_ntfs", 14, 28),
            relative_paths=("src/main.py", "README.md"),
            limits=SourceReadLimits(8, 4096, 8192),
        )

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def _begin(self):
        self.connection.execute("BEGIN IMMEDIATE")

    def _create(self, *, now=NOW, ttl=300, **changes):
        values = {
            "scope": self.scope,
            "owner_id": "synthetic-owner",
            "principal_id": "synthetic-principal",
            "client_id": "owner-browser",
            "session_fingerprint": "f" * 64,
            "now": now,
            "ttl_seconds": ttl,
        }
        values.update(changes)
        return requests.create_request(self.connection, **values)

    def _committed_request(self, **kwargs):
        self._begin()
        record = self._create(**kwargs)
        self.connection.commit()
        return record

    def test_canonical_roundtrip_is_immutable_metadata_and_creates_no_grant(self):
        record = self._committed_request()
        self.assertRegex(record.request_id, r"^[0-9a-f]{32}$")
        self.assertEqual(record.revision, 1)
        self.assertEqual(record.state, "pending")
        self.assertEqual(hashlib.sha256(record.record_bytes).hexdigest(), record.record_sha256)
        self.assertEqual(requests.get_request(self.connection, record.request_id), record)
        value = record.as_dict()
        self.assertEqual(value["recordKind"], "onepath.preread-source-request")
        self.assertEqual(value["authScope"], {
            "ownerId": "synthetic-owner", "principalId": "synthetic-principal",
            "clientId": "owner-browser", "sessionFingerprint": "f" * 64,
        })
        self.assertEqual(value["captureScope"], json.loads(self.scope.canonical_bytes()))
        serialized = record.record_bytes.decode("utf-8")
        for forbidden in ("sessionToken", "approvalId", "approvalSecret", "sourceBytes", "sha256\":\"f"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)
        value["authScope"]["ownerId"] = "mutated caller copy"
        self.assertEqual(requests.get_request(self.connection, record.request_id).as_dict()["authScope"]["ownerId"],
                         "synthetic-owner")

    def test_create_requires_transaction_and_rollback_removes_request(self):
        with self.assertRaises(requests.SourcePrereadRequestError):
            self._create()
        self._begin()
        with self.assertRaises(requests.SourcePrereadRequestError):
            self._create(session_fingerprint="raw-session-token")
        self.connection.rollback()
        self._begin()
        record = self._create()
        self.connection.rollback()
        with self.assertRaises(requests.SourcePrereadRequestError):
            requests.get_request(self.connection, record.request_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cae_source_approvals").fetchone()[0], 0)

    def test_duplicate_generated_id_is_rejected_without_accepting_caller_id(self):
        fixed = uuid.UUID("0123456789abcdef0123456789abcdef")
        with patch.object(requests.uuid, "uuid4", return_value=fixed):
            first = self._committed_request()
            self._begin()
            with self.assertRaises(requests.SourcePrereadRequestError) as caught:
                self._create()
            self.assertEqual(caught.exception.code, "DUPLICATE_REQUEST_ID")
            self.connection.rollback()
        self.assertEqual(requests.get_request(self.connection, first.request_id).state, "pending")

    def test_decline_and_expiry_use_revision_digest_cas(self):
        declined = self._committed_request()
        self._begin()
        with self.assertRaises(requests.SourcePrereadRequestError):
            requests.decline_request(
                self.connection, request_id=declined.request_id, expected_revision=1,
                expected_record_sha256="0" * 64, now=NOW + timedelta(seconds=1),
            )
        self.connection.rollback()
        self._begin()
        result = requests.decline_request(
            self.connection, request_id=declined.request_id, expected_revision=declined.revision,
            expected_record_sha256=declined.record_sha256, now=NOW + timedelta(seconds=1),
        )
        self.connection.commit()
        self.assertEqual((result.revision, result.state), (2, "declined"))
        self._begin()
        with self.assertRaises(requests.SourcePrereadRequestError):
            requests.decline_request(
                self.connection, request_id=declined.request_id, expected_revision=1,
                expected_record_sha256=declined.record_sha256, now=NOW + timedelta(seconds=2),
            )
        self.connection.rollback()

        expiring = self._committed_request(ttl=2)
        self._begin()
        with self.assertRaises(requests.SourcePrereadRequestError) as caught:
            requests.expire_request(
                self.connection, request_id=expiring.request_id, expected_revision=1,
                expected_record_sha256=expiring.record_sha256, now=NOW + timedelta(seconds=1),
            )
        self.assertEqual(caught.exception.code, "REQUEST_NOT_EXPIRED")
        self.connection.rollback()
        self._begin()
        expired = requests.expire_request(
            self.connection, request_id=expiring.request_id, expected_revision=1,
            expected_record_sha256=expiring.record_sha256, now=NOW + timedelta(seconds=2),
        )
        self.connection.commit()
        self.assertEqual((expired.revision, expired.state), (2, "expired"))

    def test_malformed_payload_duplicate_keys_and_scope_tampering_fail_fixed(self):
        record = self._committed_request()
        tampered_scope = record.record_bytes.replace(b"src/main.py", b"src/other.py")
        self.connection.execute(
            "UPDATE cae_source_preread_requests SET record_json=? WHERE request_id=?",
            (sqlite3.Binary(tampered_scope), record.request_id),
        )
        with self.assertRaises(requests.SourcePrereadRequestError) as caught:
            requests.get_request(self.connection, record.request_id)
        self.assertEqual(caught.exception.code, "REQUEST_CORRUPT")

        invalid_scope = json.loads(record.record_bytes)
        invalid_scope["captureScope"]["schemaVersion"] = 2
        invalid_scope_bytes = json.dumps(
            invalid_scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        self.connection.execute(
            "UPDATE cae_source_preread_requests SET record_json=?,record_sha256=? WHERE request_id=?",
            (sqlite3.Binary(invalid_scope_bytes), hashlib.sha256(invalid_scope_bytes).hexdigest(), record.request_id),
        )
        with self.assertRaises(requests.SourcePrereadRequestError) as caught:
            requests.get_request(self.connection, record.request_id)
        self.assertEqual(caught.exception.code, "REQUEST_CORRUPT")

        duplicate = record.record_bytes.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1')
        self.connection.execute(
            "UPDATE cae_source_preread_requests SET record_json=?,record_sha256=? WHERE request_id=?",
            (sqlite3.Binary(duplicate), hashlib.sha256(duplicate).hexdigest(), record.request_id),
        )
        with self.assertRaises(requests.SourcePrereadRequestError) as caught:
            requests.get_request(self.connection, record.request_id)
        self.assertEqual(caught.exception.code, "REQUEST_CORRUPT")

    def test_catalog_lookup_database_error_maps_to_fixed_failure(self):
        class CatalogFailureConnection(sqlite3.Connection):
            fail_catalog = False

            def execute(self, sql, parameters=()):
                if self.fail_catalog and "sqlite_master" in sql:
                    raise sqlite3.OperationalError("synthetic catalog failure")
                return super().execute(sql, parameters)

        connection = sqlite3.connect(
            Path(self.temp.name) / "catalog-failure.sqlite3", factory=CatalogFailureConnection,
        )
        try:
            connection.row_factory = sqlite3.Row
            requests.initialize(connection)
            connection.fail_catalog = True
            with self.assertRaises(requests.SourcePrereadRequestError) as caught:
                requests.get_request(connection, "0" * 32)
            self.assertEqual(caught.exception.code, "DATABASE_FAILURE")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
