from __future__ import annotations

import unittest

from app.runtime.linux_isolation_prerequisites import (
    probe_ubuntu24_read_only_isolation_prerequisites,
)


class LinuxIsolationPrerequisiteProbeTests(unittest.TestCase):
    def fixture(self):
        return {
            "/etc/os-release": 'ID="ubuntu"\nVERSION_ID="24.04"\nNAME="Ubuntu"\n',
            "/sys/kernel/security/lsm": "capability,landlock,lockdown,yama,apparmor,bpf\n",
            "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1\n",
            "/proc/sys/kernel/seccomp/actions_avail": "kill_process kill_thread trap errno user_notif trace log allow\n",
            "/proc/self/cgroup": "0::/user.slice/user-1000.slice/session.scope\n",
        }

    def run_probe(self, files, *, system="Linux", kernel="6.8.0-31-generic"):
        reads = []

        def read(path):
            reads.append(path)
            if path not in files:
                raise FileNotFoundError(path)
            return files[path]

        result = probe_ubuntu24_read_only_isolation_prerequisites(
            platform_system=system, kernel_release=kernel, text_reader=read,
        )
        return result, reads

    def test_reports_only_metadata_and_keeps_enforcement_unproven(self):
        result, reads = self.run_probe(self.fixture())
        self.assertEqual(result.host_status, "VISIBLE_USERSPACE_METADATA_ONLY")
        self.assertEqual(result.native_host_identity, "UNKNOWN")
        self.assertEqual(result.trust_status, "UNTRUSTED_OBSERVATIONS_ONLY")
        self.assertEqual(result.os_id, "ubuntu")
        self.assertEqual(result.os_version_id, "24.04")
        self.assertTrue(result.apparmor_lsm_observed)
        self.assertEqual(result.apparmor_userns_restriction, "ENABLED_POLICY_OBSERVED")
        self.assertEqual(result.user_namespace_creation, "UNPROVEN")
        self.assertTrue(result.landlock_lsm_observed)
        self.assertEqual(result.landlock_syscall_availability, "UNKNOWN")
        self.assertEqual(result.landlock_enforcement, "UNPROVEN")
        self.assertEqual(result.seccomp_actions_available[0], "kill_process")
        self.assertEqual(result.seccomp_enforcement, "UNPROVEN")
        self.assertTrue(result.cgroup_v2_observed)
        self.assertIsNone(result.cgroup_controllers)
        self.assertEqual(result.cgroup_delegation, "UNPROVEN")
        self.assertEqual(result.canary_readiness, "UNPROVEN_READ_ONLY_METADATA_ONLY")
        self.assertEqual(len(reads), len(set(reads)))
        self.assertTrue(all(path.startswith(("/etc/", "/proc/", "/sys/")) for path in reads))
        self.assertFalse(any(path.startswith("/sys/fs/cgroup") for path in reads))

    def test_windows_blocks_without_reading_metadata(self):
        result, reads = self.run_probe({}, system="Windows")
        self.assertEqual(result.host_status, "BLOCKED_UNSUPPORTED_HOST")
        self.assertEqual(reads, [])
        self.assertEqual(result.canary_readiness, "UNPROVEN")

    def test_non_ubuntu_and_wrong_version_are_blocked(self):
        for os_release in ('ID="debian"\nVERSION_ID="12"\n', 'ID=ubuntu\nVERSION_ID=22.04\n'):
            files = self.fixture()
            files["/etc/os-release"] = os_release
            result, _ = self.run_probe(files)
            self.assertEqual(result.host_status, "BLOCKED_UNSUPPORTED_DISTRIBUTION")
            self.assertEqual(result.canary_readiness, "UNPROVEN_READ_ONLY_METADATA_ONLY")

    def test_missing_malformed_or_permission_denied_metadata_stays_unknown(self):
        cases = [
            {},
            {**self.fixture(), "/etc/os-release": 'ID="ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\n'},
            {**self.fixture(), "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "maybe"},
            {**self.fixture(), "/sys/kernel/security/lsm": "apparmor,../landlock"},
        ]
        for files in cases:
            result, _ = self.run_probe(files)
            self.assertEqual(result.canary_readiness, "UNPROVEN_READ_ONLY_METADATA_ONLY")
            if not files:
                self.assertEqual(result.host_status, "UNKNOWN_HOST_METADATA")
        files = self.fixture()

        def denied(path):
            if path == "/etc/os-release":
                raise PermissionError(path)
            return files[path]

        result = probe_ubuntu24_read_only_isolation_prerequisites(
            platform_system="Linux", kernel_release="6.8.0-31-generic", text_reader=denied,
        )
        self.assertEqual(result.host_status, "UNKNOWN_HOST_METADATA")

    def test_cgroup_root_membership_is_parsed_without_controller_reads(self):
        files = self.fixture()
        files["/proc/self/cgroup"] = "0::/\n"
        result, reads = self.run_probe(files)
        self.assertTrue(result.cgroup_v2_observed)
        self.assertIsNone(result.cgroup_controllers)
        self.assertIsNone(result.cgroup_child_controllers)
        self.assertEqual(result.cgroup_delegation, "UNPROVEN")
        self.assertFalse(any(path.startswith("/sys/fs/cgroup") for path in reads))

    def test_invalid_cgroup_membership_never_builds_a_dynamic_path(self):
        for membership in ("0::/../../tmp\n", "0::relative\n", "0::/ok\n0::/duplicate\n"):
            files = self.fixture()
            files["/proc/self/cgroup"] = membership
            result, reads = self.run_probe(files)
            self.assertIsNone(result.cgroup_v2_observed)
            self.assertFalse(any(path.startswith("/sys/fs/cgroup") for path in reads))

    def test_missing_cgroup_or_kernel_capability_metadata_is_not_inferred(self):
        files = self.fixture()
        del files["/proc/self/cgroup"]
        del files["/proc/sys/kernel/seccomp/actions_avail"]
        result, _ = self.run_probe(files)
        self.assertIsNone(result.cgroup_v2_observed)
        self.assertIsNone(result.seccomp_actions_available)
        self.assertEqual(result.landlock_syscall_availability, "UNKNOWN")
        self.assertEqual(result.cgroup_delegation, "UNPROVEN")

if __name__ == "__main__":
    unittest.main()
