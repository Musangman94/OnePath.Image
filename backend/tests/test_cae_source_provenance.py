import hashlib
import json
import unittest

from app.services import cae_source_provenance as provenance


def approved(path, data=b"source"):
    return provenance.ApprovedSourceFileV1(path, len(data), hashlib.sha256(data).hexdigest())


def index_record(path, *, tag="H", mode="100644", oid="a" * 40, stage=0):
    return f"{tag} {mode} {oid} {stage}\t{path}".encode("utf-8") + b"\0"


class CaeSourceProvenanceTests(unittest.TestCase):
    def test_builtin_policy_is_versioned_canonical_and_content_hashed(self):
        raw = provenance.BUILTIN_SOURCE_PROFILE_V1_JSON
        value = json.loads(raw.decode("utf-8"))
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(value["profileId"], "onepath-source-selection-v1")
        self.assertEqual(raw, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(provenance.BUILTIN_SOURCE_PROFILE_V1_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(value["limits"], {
            "maxFiles": 32, "maxFileBytes": 1_048_576, "maxTotalBytes": 4_194_304,
            "maxPathUtf8Bytes": 1024, "maxIndexOutputBytes": 1_048_576,
            "maxIndexRecords": 4096,
        })

    def test_allowlist_permits_source_extensions_and_owner_credentials_source(self):
        values = [
            approved("src/owner_credentials.py"), approved("docs/NOTICE"),
            approved("src/main.TS"), approved("queries/schema.sql"),
        ]
        result = provenance.validate_approved_files(values)
        self.assertEqual([entry.path for entry in result], sorted(entry.path for entry in values))

    def test_private_hidden_binary_and_unknown_extension_paths_fail_closed(self):
        denied = (
            ".env", ".git/config", "venv/pkg.py", "node_modules/pkg.js",
            "private/notes.md", "memories/context.md", "models/weights.json",
            "data/backups/file.sql", "credentials.json", "credentials.token",
            "secrets.txt", "id_rsa", "id_ed25519", "archive.zip", "image.png",
            "store.sqlite", "module.pyc", "secret.key", "owner_credentials.pem",
        )
        for path in denied:
            with self.subTest(path=path), self.assertRaises(provenance.SourceProvenanceError):
                provenance.validate_approved_files([approved(path)])

    def test_build_and_dependency_roots_are_denied_at_root_and_nested_casefolded(self):
        roots = ("build", "dist", "vendor", "target", "deps", "third_party")
        paths = []
        for root in roots:
            paths.extend((f"{root}/module.py", f"src/{root.upper()}/module.py"))
        for path in paths:
            with self.subTest(path=path), self.assertRaises(provenance.SourceProvenanceError):
                provenance.validate_approved_files([approved(path)])
        allowed = [approved("build.py"), approved("target.py"), approved("owner_credentials.py")]
        self.assertEqual(len(provenance.validate_approved_files(allowed)), 3)

    def test_path_syntax_unicode_casefold_ancestor_and_windows_rules(self):
        denied_lists = (
            [approved("../main.py")], [approved("/main.py")], [approved(r"src\main.py")],
            [approved("C:/main.py")], [approved("src/file:stream.py")],
            [approved("src//main.py")], [approved("src/./main.py")],
            [approved("src/../main.py")], [approved("src/CON.txt")],
            [approved("src/trailing. ")], [approved("cafe\u0301.py")],
            [approved("A.py"), approved("a.PY")],
            [approved("src.py"), approved("src.py/main.py")],
            [approved("src.py/main.py"), approved("SRC.PY")],
        )
        for entries in denied_lists:
            with self.subTest(paths=[entry.path for entry in entries]), self.assertRaises(provenance.SourceProvenanceError):
                provenance.validate_approved_files(entries)
        self.assertEqual(provenance.validate_approved_files([approved("caf\u00e9.py")])[0].path, "caf\u00e9.py")

    def test_limits_use_actual_integers_and_enforce_count_path_file_and_total_caps(self):
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.validate_approved_files([provenance.ApprovedSourceFileV1("x.py", True, "0" * 64)])
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.validate_approved_files([provenance.ApprovedSourceFileV1("x.py", -1, "0" * 64)])
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.validate_approved_files([provenance.ApprovedSourceFileV1("x.py", 1_048_577, "0" * 64)])
        too_many = [approved(f"src/{index}.py", b"") for index in range(33)]
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.validate_approved_files(too_many)
        over_total = [approved(f"src/{index}.py", b"x" * 1_048_576) for index in range(4)]
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.validate_approved_files(over_total + [approved("src/extra.py", b"x")])
        at_total = provenance.validate_approved_files(over_total)
        self.assertEqual(sum(item.size_bytes for item in at_total), provenance.MAX_TOTAL_BYTES)
        max_path = "a" * (provenance.MAX_PATH_UTF8_BYTES - len(".py")) + ".py"
        self.assertEqual(len(provenance.validate_approved_files([approved(max_path)])[0].path.encode("utf-8")), 1024)

    def test_valid_tracked_untracked_sha1_and_sha256_observations_are_canonical(self):
        files = [
            approved("src/main.py", b"print('hi')\n"),
            approved("README.md", b"notes"),
            approved("scripts/run.sh", b"#!/bin/sh\n"),
        ]
        oid1 = "1" * 40
        raw1 = (index_record("src/main.py", oid=oid1)
                + index_record("scripts/run.sh", mode="100755", oid="3" * 40)
                + index_record(".gitignore", oid="2" * 40))
        observation1 = provenance.parse_cached_index(raw1, files, object_format="sha1", head_oid="a" * 40)
        self.assertEqual(observation1.schema_version, 1)
        self.assertEqual(observation1.source_policy_profile_sha256, provenance.BUILTIN_SOURCE_PROFILE_V1_SHA256)
        self.assertEqual(observation1.object_format, "sha1")
        self.assertEqual(observation1.raw_index_output_sha256, hashlib.sha256(raw1).hexdigest())
        self.assertEqual([(entry.path, entry.origin, entry.provenance_status) for entry in observation1.entries], [
            ("README.md", "untracked", "approved-index-absent"),
            ("scripts/run.sh", "tracked", "index-entry"),
            ("src/main.py", "tracked", "index-entry"),
        ])
        self.assertEqual(observation1.entries[2].index_oid, oid1)
        self.assertEqual(observation1.entries[1].mode, "100755")
        self.assertNotIn("clean", observation1.canonical_json().decode("utf-8").lower())
        self.assertNotIn("print('hi')", observation1.canonical_json().decode("utf-8"))

        oid2 = "b" * 64
        raw2 = index_record("src/main.py", oid=oid2)
        observation2 = provenance.parse_cached_index(raw2, files, object_format="sha256", head_oid="c" * 64)
        observation2_repeat = provenance.parse_cached_index(raw2, list(reversed(files)), object_format="sha256", head_oid="c" * 64)
        self.assertEqual(observation2.object_format, "sha256")
        self.assertEqual(observation2.head_oid, "c" * 64)
        self.assertEqual(observation2.entries[2].index_oid, oid2)
        self.assertEqual(observation2.canonical_json(), observation2_repeat.canonical_json())

    def test_index_parser_rejects_aliases_sparse_ancestors_conflicts_and_unsupported_entries(self):
        selected = [approved("src/main.py")]
        bad_index_outputs = (
            index_record("SRC/main.py"),
            index_record("src/", mode="040000"),
            index_record("src/main.py", stage=2),
            index_record("src/main.py", mode="120000"),
            index_record("src/main.py", mode="160000"),
            index_record("src/main.py", mode="100644", tag="S"),
            index_record("src/main.py", mode="100644", tag="h"),
            index_record("src/main.py", mode="100644", oid="0" * 40),
            index_record("src/main.py") + index_record("src/main.py", stage=2),
        )
        for raw in bad_index_outputs:
            with self.subTest(raw=raw[:40]), self.assertRaises(provenance.SourceProvenanceError):
                provenance.parse_cached_index(raw, selected, object_format="sha1", head_oid="a" * 40)

    def test_index_parser_rejects_malformed_framing_oid_and_unknown_tags(self):
        for raw in (b"no-nul", b"\0", index_record("x.py", oid="g" * 40), index_record("x.py", tag="?")):
            with self.subTest(raw=raw[:30]), self.assertRaises(provenance.SourceProvenanceError):
                provenance.parse_cached_index(raw, [approved("src/main.py")], object_format="sha1", head_oid="a" * 40)
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.parse_cached_index(b"", [approved("src/main.py")], object_format="sha256", head_oid="a" * 40)

    def test_index_output_and_record_count_are_bounded(self):
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.parse_cached_index(b"x" * (provenance.MAX_INDEX_OUTPUT_BYTES + 1),
                                          [approved("src/main.py")], object_format="sha1", head_oid="a" * 40)
        raw = b"".join(index_record(f"other/{index}.py") for index in range(provenance.MAX_INDEX_RECORDS + 1))
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.parse_cached_index(raw, [approved("src/main.py")], object_format="sha1", head_oid="a" * 40)

    def test_git_blob_oid_uses_raw_bytes_and_distinguishes_line_endings(self):
        for object_format in ("sha1", "sha256"):
            expected = hashlib.new(object_format, b"blob 0\0").hexdigest()
            self.assertEqual(provenance.compute_git_blob_oid(b"", object_format), expected)
        lf = b"line\n"
        crlf = b"line\r\n"
        self.assertNotEqual(provenance.compute_git_blob_oid(lf, "sha1"), provenance.compute_git_blob_oid(crlf, "sha1"))
        self.assertNotEqual(provenance.compute_git_blob_oid(lf, "sha256"), provenance.compute_git_blob_oid(crlf, "sha256"))
        with self.assertRaises(provenance.SourceProvenanceError):
            provenance.compute_git_blob_oid(b"x" * (provenance.MAX_FILE_BYTES + 1), "sha1")


if __name__ == "__main__":
    unittest.main()
