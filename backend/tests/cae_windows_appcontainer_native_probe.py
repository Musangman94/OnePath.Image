"""Explicit engineering smoke CLI. Never discovered/executed as a unit test.

Run only after independent review and immediate external executable/source pin
checks. Owns one fixed System32 cmd /d literal-echo child and a fresh AppContainer profile;
no ACL changes, installs, owner-source reads, candidate or compiler execution.
"""
import argparse
import hashlib
import json
import platform
from pathlib import Path

from app.runtime.windows_appcontainer_smoke import run_windows_appcontainer_smoke


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-binary-sha256", required=True)
    parser.add_argument("--evidence-directory", required=True, type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    evidence = arguments.evidence_directory.resolve()
    evidence.mkdir(exist_ok=False)  # Never reuse a failed run or overwrite evidence.
    paths = [
        "backend/app/runtime/win32_jobs.py",
        "backend/app/runtime/windows_appcontainer_smoke.py",
        "backend/app/runtime/windows_appcontainer_profiles.py",
        "backend/app/runtime/windows_appcontainer_token_check.py",
        "backend/app/runtime/windows_appcontainer_canary_plan.py",
        "backend/tests/cae_windows_appcontainer_native_probe.py",
    ]
    provenance = {
        "platform": platform.platform(), "python": platform.python_version(),
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
        "limits": "fixed trusted smoke only; not native sandbox acceptance",
    }
    (evidence / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    result = run_windows_appcontainer_smoke(
        expected_binary_sha256=arguments.expected_binary_sha256,
        journal_path=evidence / "journal.jsonl",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
