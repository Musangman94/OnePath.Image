# OnePath v2.3 CAE contract and R5 Evidence Bundle Planner protocol

Status: owner-approved architecture and contract baseline. R0 is documentation and contract validation only. This document does not establish an implemented CAE runtime, trusted sandbox, experiment result, or product acceptance. The canonical delivery stages and product direction are summarized in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Authority and machine contract

OnePath's native Gandalf is the product's sole product lead. Codex Architecture Gandalf coordinates development; named Departments implement bounded assignments, and the owner reviews. Neither the product agent nor its evolution capability may edit its own trusted evaluation authority, permission policy, approval mechanism, or OS trust controls.

Python DTO definitions are the machine-authoritative field shapes. The implemented R0 schema is `backend/app/schemas/cae.py`. It supplies strict structural validators for the DTOs below; it does not implement runtime execution, admission authorization, or sandbox attestation. This document describes approved semantics and deliberately does not duplicate JSON field definitions. The DTO names are:

- `ArtifactRef`, `EvolutionTargetV2`, `CandidateManifestV2`
- `ResearchAuthorizationV1`, `SourceFileEntryV1`, `SourceStateManifestV1`
- `BuildEnvironmentV1`, `BuildManifestV1`, `EngineeringPilotProfileV1`
- `ResearchProposalV1`, `ReviewDecisionV1`

The schema validates field shape and selected invariants, including identifier/hash syntax, normalized and unique paths, uniqueness constraints, UTC authorization timestamps, research records that cannot authorize production promotion, cloud-consent consistency, and campaign-budget arithmetic. A syntactically valid SHA-256 string does not prove that referenced content exists, matches the digest, or is authorized; DTO validation is not grant enforcement. Existing application API v1 DTOs, task canonicalization, run lifecycle, and SSE behavior remain unchanged.

### Source-state artifact-reference mapping

`CandidateManifestV2.sourceStateRef` references the canonical, full `SourceStateManifestV1` descriptor. The descriptor's `manifestArtifactRef` references a `SourcePayloadIndexV1` artifact whose canonical JSON shape is `{"schemaVersion":1,"files":[...SourceFileEntryV1 entries...]}`. Sort `files` by normalized path. The index entries must equal the descriptor's complete `files` list, and each entry's `sha256` must resolve to raw file bytes with the same content hash in the same content-addressed store.

Store each source payload object with `ArtifactRef` values `id="source-<lowercase-hex-sha256>"`, matching `sha256`, and `version=1`. This ID prefix is local to the source-artifact store; it is not a global rule for generic `ArtifactRef` values. Do not create a self-reference for the descriptor: return its `ArtifactRef` separately, while its `manifestArtifactRef` points to the payload index.

Serialize the index as UTF-8 JSON with sorted object keys, no insignificant whitespace, `ensure_ascii=False`, and `allow_nan=False`. Sort file entries by path. Do not silently normalize Unicode or paths; the caller supplies a stable `sourceStateId` and explicit metadata. Store only bytes already supplied to the store, using the caller's existing SQLite connection or explicit initialization. Writes must be transactional and idempotent; reads must verify the referenced bytes. This defines logical content integrity, not physical immutability, authentication, authorization, sandboxing, or platform-release proof.

**Implementation status: implemented and independently verified within the bounded source-artifact scope.** The module is `backend/app/storage/cae_source_artifacts.py` (SHA-256 `5D70CCEE71BA75F3C1ECCAF4BA6718BE57127C3832EB731DD68AD3BC2128434A`). The independent review passed 8 focused tests, compile, and three scratch-SQLite checks for caller transaction preservation, rollback, and corrupted-object idempotence; see [the source-artifact risk report](../.local/verification/cae-v23-r1-source-artifact-risk.md) (SHA-256 `72E7B647BD51F14891634117181BFF35736B2FB59BC67904C50BC8B1F8E3F300`). This store is not a filesystem collector or grant issuer. Source exclusions, symlink/reparse handling, race-safe collection, and approvals remain mandatory before any source collection. No harvesting or candidate execution is authorized here. Existing R0 DTO shapes are unchanged; the earlier 13-test R0 verification was not rerun for this clarification. The new storage proof does not establish owner-database activation, application startup/API integration, authorization, sandboxing, or platform-release behavior.

### Expected-byte source reader

**Implementation status: implemented and independently verified within a bounded Windows reader scope.** `backend/app/services/cae_source_files.py` (SHA-256 `5C2E60DDAD912DD301B6B5AE03E376A5C4B0752BEEF199728FD1F13327255579`) reads only declared files from a caller-supplied source root and verifies expected size and SHA-256. The independent [source-reader risk report](../.local/verification/cae-v23-r1-source-reader-risk.md) (SHA-256 `035D22BBF82DE3964E8F13A7A502C36BC9AFD661B6E62E17D970258F9FDCF671`) records 11 tests: 8 passed, 3 skipped (two POSIX-only checks and Windows file-symlink creation unavailable). Native Windows NTFS positive-read and junction-rejection checks passed. A bounded scratch probe observed held handles block file writes and directory rename during a read, then release after a hash failure.

This is not an owner grant issuer or a complete filesystem collector. A fresh independent static review of the current Git metadata runner (`D0F3B6BA2941219AACD244335690AECA3C99CD97DFC07610AA894B8E24DDEB71`), its focused test source (`48FAEC184DE64E5BF447A90EF05DB6E8D6AB4447CA27D2A8744BEAFA3596F5A7`), and the reused Windows reader (`5C2E60DDAD912DD301B6B5AE03E376A5C4B0752BEEF199728FD1F13327255579`) passed 2/2 static criteria. It verified reader identity reuse and that a mismatched root identity prevents process creation. The earlier static review's Windows identity mismatch and cross-platform FAIL apply to earlier runner/test hashes and remain historical. The fresh review did not rerun tests; no native Git launch or probe occurred. Source-policy exclusions, approval ledger, and session-proof helper exist as separate bounded components, but authenticated source capture is not integrated. Whole-snapshot atomicity, application/API integration, sandboxing, descendant cleanup, actual executable-version attestation, root/executable check-to-open race elimination, and platform-release behavior remain unproven. Native POSIX behavior remains unverified; no startup, owner database, API, or source-collection flow was enabled by this reader work.

### Bounded source policy and Git-index provenance

**Implementation status: implemented and independently verified within a bounded metadata scope.** [`backend/app/services/cae_source_provenance.py`](../backend/app/services/cae_source_provenance.py) (SHA-256 `174D0EF50B1FFB538602C5F778EB80181697716E787E5C6B201C10AC05786A21`) applies the built-in profile with digest `358547d2e8e66ab503cd86dcff0cc6326318d98d0f5ddd06b0db1b5ea0a898a7`: at most 32 files, 1 MiB per file, 4 MiB total, and 1024 UTF-8 bytes per path. It denies credential, private-memory, data, model, build, and dependency directories. The profile cannot be overridden. This path policy is not a content-level secret scan and does not claim selected files are secret-free.

The parser accepts only caller-supplied index metadata, bounded to 1 MiB and 4096 entries. It accepts stage-0 regular tracked entries or explicitly approved absent untracked paths; it rejects selected case aliases, unsupported index states, and sparse ancestors. For tracked content it compares a locally computed raw Git blob hash; it does not run Git clean/dirty or conversion filters. The source report found that `git status` may execute filters, so a future trusted metadata-only runner must use cached index data without invoking status, diff, or conversion. The runner source exists. A fresh independent static review of the current runner, test-source, and reused-reader hashes passed 2/2 static criteria; it verified root-identity reuse and rejection before process creation on mismatch. The earlier report's Windows mismatch applied to earlier artifact hashes. The fresh reviewer did not rerun tests, and no native Git launch was performed or accepted. The runner's expected-version label is caller-supplied metadata, not executable-version attestation; root/executable check-to-open intervals, descendant cleanup, and OS sandboxing remain unproven. See the [source metadata runner risk report](../.local/verification/cae-v23-r1-source-metadata-runner-risk.md) for the dated evidence and preserved history. [The focused test module](../backend/tests/test_cae_source_provenance.py) (SHA-256 `D7F69A082E33AFB33E75A8BF5241AD12E1771F762104398FFA5592A83672E5F5`) passed 11 isolated tests in the independent review.

`SourceStateManifestV1` is unchanged. A companion provenance artifact is planned but not implemented; do not smuggle provenance IDs into existing DTOs. This module is not owner approval, actual Git-HEAD or file-system attestation, a collector, a sandbox, or complete R1 acceptance.

### One-use source-selection approval ledger

**Implementation status: implemented and independently verified within a bounded storage scope.** The internal SQLite API is `initialize`, `issue_approval`, and `consume_approval` in [`backend/app/storage/cae_source_approvals.py`](../backend/app/storage/cae_source_approvals.py) (source SHA-256 `3161E6DF3D4692B53C8EFEC5939AEB48B00A1F66F3716B9A94F16415A47FBC54`). `initialize` requires an idle connection. Issue and consume require an already-active caller-owned transaction; this module never commits or rolls back it. The default approval lifetime is 300 seconds and the maximum is 900 seconds. Issuance returns a random 32-byte one-use secret and persists only its SHA-256 digest.

The canonical selection binding covers owner, principal, client and session IDs; root path and platform/device/file identity; the sorted exact file paths, SHA-256 values, sizes and tracked/untracked/generated origins; optional source revision; provenance reference; and exclusion-policy version and reference. Consumption checks the secret, complete canonical selection, expiry, and single-use state. These fields are caller-attested inputs, not self-verifying. The separate internal `LocalAuthService.authenticate_session` seam can revalidate a live stored owner session on the caller's configured database connection; see [the local-auth contract](LOCAL_AUTH.md). It does not prove that source metadata, Git state, or exclusions are correct.

The bounded standard-library `unittest` suite in [`backend/tests/test_cae_source_approvals.py`](../backend/tests/test_cae_source_approvals.py) (test SHA-256 `071D02E3B4608DDE12271EE686DBC57559E50CCECD5E7258A1D8558A64C45ED9`) passed all 9 tests in the recorded review, including wrong-scope/secret rejection, expiry and replay, caller-transaction rollback, concurrent single consumption, and ancestor-path collision rejection. Additional temporary-SQLite probes covered reversed/deeper/casefolded path-prefix collisions and valid siblings. The suite can be run from the repository root with `PYTHONPATH=backend` using `python -m unittest backend.tests.test_cae_source_approvals -v`; it uses this module, the test file, Python's standard library and temporary databases, without starting the application or importing the rest of the untracked backend.

This ledger is not a grant issuer or source collector. The session-proof helper is internal only; no external source route or capture integration is wired. The bounded evidence does not establish owner-confirmation UX, end-to-end authorization, Git state, exclusion correctness, source-byte capture, OS isolation, application endpoint/startup integration, or native-platform/release behavior. Those remain separate R1 acceptance gates; no source collection or candidate execution is authorized by this implementation status.

## 2. V1 CAE boundary

CAE has five engines: **Experience**, **Reflection**, **Assimilation**, **Evaluation**, and **EvolutionController**. Parallel, Idle, and Forced are scheduling modes, not engines. V1 introduces no sixth engine, framework, database, or separate authority layer.

Operational evolution and engineering evolution are both first-class V1 capabilities. Intelligence-research extension points are reserved seams only; they are not advanced V1 behavior and imply no AGI claim. Windows and native Ubuntu 24.04 are joint V1 platforms. WSL can support preliminary exploration but is not native Ubuntu evidence.

Supported engineering candidate forms are executable packages, source patches, and algorithm experiments. An advanced-learning handler may be represented as a proposal or extension point, but its execution is unavailable in V1.

Research may propose unconventional or fundamental alternatives. Each such proposal preserves a protected baseline and states a credible hypothesis, bounded comparative protocol, independent review, resource requirements, and owner decision point. No alternative may be silently discarded solely because it is outside a current roadmap.

## 3. Candidate, authorization, and promotion semantics

Candidate semantics include evolution type; a registered target kind and payload schema reference; baseline, source-state, environment, build recipe, evaluation protocol, and resource profiles; scope; requested capabilities; intended promotion authority; the four improvement dimensions; source, dependency, and license provenance; and rollback information. Exact serialized fields belong only in the Python DTO source.

Research authorization grants permission for a bounded private experiment; it does not grant production-promotion authority. A production activation requires owner approval bound to the exact artifact hash, evaluation and security evidence, and a rollback plan. Trusted OS controls, evaluation authority, permission policy, and approval mechanisms are protected from autonomous edits.

Operational database activation must use compare-and-swap (CAS) safeguards: bind the candidate, baseline, evaluation, policy, and scope; require a single-use nonce and expected revision in one transaction; and record an auditable outcome. This activation path is required but unimplemented. Code production activation is unsupported until there is a trusted staged-release and journaling gate. SQLite alone cannot make a database update and deployment of code files atomic.

Candidate compilation and execution are excluded until per-OS isolation has been attested. Python `-I`/`-S` flags and a virtual environment are not a sandbox. Each platform needs a `SandboxAttestation` covering controller identity and enforced policy, including tokens or profiles, filesystem maps and handles, secrets, network, process controls, and quotas. Before candidate compilation, negative probes must demonstrate denial for fake canaries, protected inputs, evaluator writes, network access, sandbox escape, timeout, and crash. DTO metadata is never proof of OS isolation.

## 4. Frozen R5 protocol: Evidence Bundle Planner

The first planned engineering experiment is a pure-Python CLI planner protocol, frozen at R0 for execution at R5. No experiment ran in R0. It receives authorized, prefiltered unique IDs, positive integer cost units, dependencies, provenance, required tags, and a budget. It returns known unique IDs whose dependency closure is complete and whose summed declared cost is within budget. Cost units are not actual prompt tokens and do not establish production retrieval behavior.

Two deterministic baselines are compared:

1. **B0 chronological first fit:** consider candidates in input order; add a candidate only when its transitive dependency closure fits. Otherwise skip it. Do not backtrack.
2. **B1 greedy gain:** rank by newly covered required tags divided by dependency-closure cost. Resolve ties by higher gain, lower cost, then lexicographic ID. Skip zero-gain or non-fitting candidates and return selected IDs sorted lexicographically.

An exact oracle is limited to candidate sets with `N <= 16`; stress cases may reach `N <= 64` and do not use the oracle. The fixed campaign has 12 public development cases, 24 held-out cases (four six-case families: dependency, overlap, tight budget, and cyclic/disconnected), and 12 stress cases. At most three development candidates may be considered; select one exact candidate hash before opening held-out results. Campaign held-out cases are never training data.

Independent evaluation controls the comparison and reports results; it does not authorize a candidate. Valid compilation, execution, security, interface, reproducibility, and an owner decision may still end in `no_gain` or rejection. A compile or execution failure is not evidence of a successful improvement.

Record efficiency, reliability, capability, and productivity separately. Token or source-edit changes alone are not intelligence evidence. A positive capability result requires at least two additional held-out successes over B1, no case solved by B1 lost, no per-family regression, zero contract/security/reliability regressions, median latency no greater than twice B1, and compliance with resource caps. Otherwise report the measured trade-off without promotion.

An operational retrieval pilot is a separate gate: at least 5% median actual-prompt-token reduction over five fixtures and three paired runs, unchanged quality and security, explicit owner activation, and a separate rollback path.

## 5. Per-platform resource and safety envelope

The automated budget is capped at 20 minutes per platform. The phase caps are generation/development 360 seconds (model 324, public tests 36); compilation 60 seconds (three launches at 20 seconds); evaluation 480 seconds (324 comparison launches at one second each, 16 security launches at one second each, 60 seconds for the oracle, and 80 seconds overhead); reporting 120 seconds; and cleanup reserve 180 seconds. These caps total 1,200 seconds. The 379 launches comprise 3 compile, 36 public-development, 324 sealed-comparison (36 cases × 3 systems × 3 repeats), and 16 security launches. A slot includes launch, execution, and confirmed cleanup. If cleanup is uncertain, stop dispatching.

The total campaign model-token budget is at most 32,768 tokens. Each completion admits at most an 8,192-token context, with its output capped at 2,048 tokens within that context. Source is capped at 32 KiB per module. Compilation is capped at 512 MiB per process; candidate execution at 256 MiB and one CPU per process; scratch at 128 MiB per process; and emitted output at 64 KiB per completion. Cloud use is zero until separately granted. Preparation and independent verification are bounded jobs but count in total expense. Owner review is asynchronous and reserves no compute.

The reference hardware floor is RTX 5080 with 16 GiB VRAM, 32 GiB system RAM, at least 4,096 MiB available system RAM, and at least 512 MiB available VRAM; at most one heavy generation runs at a time. Both platforms use the same sealed source and explicit budgets, with platform-specific build hashes. Windows and native Ubuntu 24.04 each need their own proof; WSL is preliminary only. No dependency installation or service setup is authorized by this contract.

## 6. R0–R8 stage gates

| Stage | Scope and advancement evidence |
| --- | --- |
| R0 | Freeze product direction, contract semantics, DTO ownership, scope, and bounded first experiment. Documentation/contract validation only; no five-engine runtime or sandbox proof. |
| R1 | Authorization fences, arbitrary-code sandbox, and compiler-worker controls. Candidate compilation/execution stays blocked until platform isolation is attested. |
| R2 | Native jobs and their bounded, recoverable execution lifecycle. |
| R3 | Integration of all three harnesses. |
| R4 | Memory integration. Offline engineering may begin after accepted R1/R2 plus owner authorization; it need not wait for GraphRAG. Operational retrieval requires R4. |
| R5 | Joint Windows and native Ubuntu 24.04 proof, including rejection, no-change, interruption, recovery, and owner decision. |
| R6 | Cloud/Fusion and a minimal DevHub trial explicitly approved by the owner. |
| R7 | Joint package for both supported platforms. |
| R8 | Independent final verification of both platform proofs. |

Each later gate remains planned until its own current evidence and owner decisions exist. R0 passing cannot be reported as runtime, sandbox, security, or end-product acceptance.

R4 memory semantics, prototype scope, and memory-specific evaluation are defined
in [MEMORY_FOUNDATION.md](MEMORY_FOUNDATION.md); this contract does not duplicate
its logical memory fields. R5 remains the joint Windows/native-Ubuntu proof and
owner-promotion boundary. The frozen E1 planner protocol and operational retrieval
pilot above are unchanged and do not establish production retrieval or memory
completeness.

## 7. Evidence and reporting rules

Preserve historical evidence and label its original scope. The latest known status still has an unresolved UI Stop cited receipt; no new UI/runtime acceptance follows from this contract. Atomic's bounded private proof is not consumer-product acceptance. Existing memory imports are not connected to this planner. Do not claim a new CAE runtime, experiment result, sandbox, or owner approval without artifact-bound evidence.

After V1, a two-round, equal-budget study using fresh held-out cases may test compounding. It is a study design, not a release blocker and not a basis for an indefinite capability claim.

## 8. Source of truth and change control

The Python DTO schema is authoritative for serialized field names and types; this document is authoritative for the approved contract semantics and gates. Reconcile them when the schema implementation returns. Keep source interfaces and test evidence in their code and reports. Any change to protected authority, production promotion, platform scope, budgets, or acceptance gates requires an explicit owner decision recorded by the architecture owner.
