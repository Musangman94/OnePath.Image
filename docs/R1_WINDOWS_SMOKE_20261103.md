# R1 Windows native integration checkpoint — 3 November 2026

R1 is **incomplete**. This increment integrates AppContainer launch mechanics and
records actual Windows failures. It does not enable candidate execution, source
capture, a compiler worker, production activation or a SandboxAttestation.

## Delivered scope

- `win32_jobs.py` adds trusted internal creation-attribute and pre-resume hooks,
  requires the expected suspend count, and retains the Windows creation error.
- `windows_appcontainer_smoke.py` provides an explicit engineering-only native
  entry. It pins fixed System32 `cmd.exe`, uses literal `/d /q /c echo
  onepath-r1-canary`, and accepts no caller command, executable path or script.
  `/d` disables command-processor AutoRun. The required marker must match exactly.
- It requests an AppContainer with no capabilities; a noninherited Job assigned
  atomically at creation; a three-handle stdio whitelist; child-process restriction;
  finite CPU/memory/process limits with readback; and a suspended token/SID check
  before resume. UI restrictions remain enabled. A bounded wait enforces the
  controller's wall deadline while alive; no crash-proof wall timer is claimed.
- The minimal environment contains only OS-derived `SystemRoot` and
  `LOCALAPPDATA`. No parent secrets or whole environment are inherited.
- An exclusive, flushed/fsynced journal records profile intent, ownership,
  verified launch stages, child outcome and cleanup. Uncertain child cleanup
  retains resources and prevents profile deletion. A crash can still orphan a
  profile; intent alone never authorizes adoption/deletion, and no automatic
  retry/recovery controller is implemented.

The ordinary profile/token effect gates remain disabled. The new smoke entry is
explicitly effectful and only for the reviewed engineering invocation; Python
private names are not an isolation boundary. No application route calls it.

## Evidence and failures

Full source identities and the five original journals/provenance records are
preserved in [the evidence bundle](evidence/r1-windows-smoke-20261103.json).
Original local reports remain under `.local/verification/r1-astra-*`.

| Native invocation | Result |
| --- | --- |
| 1 | `whoami.exe`: process creation failed; error number was not captured. Created profile deleted. |
| 2 | Same restrictions with error diagnostics: `CreateProcessW` error 203. Created profile deleted. |
| 3 | Added OS-known `LOCALAPPDATA`: token/job checks passed, then child failed; exit status not serialized. Created profile deleted. |
| 4 | Added outcome diagnostics: AppContainer=true, matching profile SID, zero capabilities, atomic Job membership and quota readback; child exit `0xC0000142`, zero stdout/stderr. Created profile deleted. |
| 5 | Independently reviewed fixed `cmd /d` literal-echo fixture, same restrictions: identical token/job observations, exit `0xC0000142`, no marker. Created profile deleted. |

**No native smoke passed.** API-observed token/Job state and successful profile
deletion are narrower results than enforcement/containment acceptance. The exact
DLL initialization cause remains unproven. Read-only controller metadata showed
session 2, visible `WinSta0\Default`, and a non-AppContainer controller token.
No desktop, ACL, elevation, service or installation changes were made to recover.

Local synthetic checks: eight focused tests passed, including pre-resume failures,
attribute values/order, quotas, exact marker, failed/empty child, timeout,
journal failure, cleanup uncertainty and environment exclusion. Two existing
native ordinary-launcher checks passed for Unicode/stdio compatibility and
assignment-failure cleanup. These ordinary children are trusted fixtures, not
AppContainer proof. Existing token/profile unit suites were not rerun unchanged.

Independent Risk review 1 covered the original launch shape and eight checks
(six initial smoke tests plus two handle-pin tests). Review 2 covered the changed
literal fixture, minimal environment, journaling and eight current synthetic
tests. Neither reviewer ran native effects. Final delivery verification is a
separate evidence record; no whole-R1 acceptance is implied by a component review.

## Remaining gates and next action

1. Diagnose `0xC0000142` under the exact retained restrictions. The current
   isolation node has consumed primary starts 2/2 and five native invocations;
   a further implementation/native attempt requires an explicit finite recovery
   decision in the existing mission, not a renamed node or automatic retry.
2. Obtain a positive trusted canary and negative filesystem, network, escape,
   evaluator-write, process, quota, timeout and crash evidence before candidate
   or compiler execution. The fixed echo is not that containment test suite.
3. Provide native Ubuntu 24.04 execution access and its independent proof.
   No native Ubuntu host was available through this session.
4. Finish trusted owner confirmation, pre-read root authorization, retention,
   durable one-use read orchestration, revocation and crash recovery. This
   increment does not change or activate those dormant components.

Rollback: revert only this increment's committed files; no application activation
or persistent permission change needs undoing. Preserve all failed journals and
any uncertain future resources rather than deleting by inferred ownership.

## References and experiment accounting

API design follows Microsoft's [process attributes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)
and [AppContainer launch](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
documentation. Documentation is not native acceptance evidence.

All primary code, integration, debugging and local tests were performed by the
primary engineer. One Risk reviewer was reused for two passes: 10 + 4 reported
tool calls; aggregate elapsed/token usage unknown (second pass reported about
2.1 seconds of tool execution, not total wall time). Final verification overhead
is reported separately. No implementation worker was dispatched. The requested
Astra assignment is owner-authorized for this mission; the primary runtime's
exact model identity was not independently exposed by the available tools.

## Final bounded-delivery review

Fresh Verification-Worker found no blocking defect in the bounded component,
with overall PARTIAL because the native smoke and broader R1 gates remain open.
It matched all five original journal/provenance pairs to the bundle and reviewed
E1 exclusion from the diff; it did not repeat the supplied tests. Its report is
`.local/verification/r1-astra-final-1.md`. The reviewer did not locate the separate
controller metadata original. Primary subsequently compared that exact original
(`.local/verification/r1-astra-controller-metadata.json`) with the embedded object:
identical; this follow-up is a primary check, not independent verification.
Final review used 8 reported tool calls (exceeding its 6-call budget), about
4 seconds of reported tool execution, and no native effects. Across the three
review passes, 22 tool calls were reported; total wall time/tokens are unknown.
