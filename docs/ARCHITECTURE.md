# OnePath.Image architecture and delivery plan

Updated: 31 October 2026, Asia/Kuala_Lumpur.

Status: owner requirements and architecture plan; implementation and verification status are stated separately below. This document does not authorize installation, execution, deployment, or unrelated repository changes.

## Authority and working roles

This is the canonical architecture and delivery record for OnePath.Image. Read it together with [the frontend model contract](MODEL_FRONTEND_CONTRACT.md) and [the first real Gandalf chat backend contract](CHAT_BACKEND_CONTRACT.md). Current owner instructions take precedence. Historical handoffs and chat proposals are background; verify current paths and implementation before using them in commands or claims.

- The owner reviews and operates the product; named Departments implement bounded assignments under Codex Architecture Gandalf.
- OnePath.Image is a consumer product; the owner's VS Code workflow describes development, not the product's customer segment.
- OnePath's native Gandalf is the product's sole product lead. Codex's Architecture Gandalf is the sole development coordinator; named Departments implement bounded assignments and the owner reviews the work. These are separate roles and authority domains.
- This architecture record retains earlier decisions and evidence as history. The owner-approved OnePath v2.3 direction below supersedes conflicting older role, owner-build-only, and architecture-scope language. Historical acceptance claims remain historical unless updated by current evidence.

## OnePath v2.3 owner direction

This section records the approved product direction and the R0 documentation baseline. It does not claim runtime implementation, sandbox proof, experiment results, or product acceptance.

- Windows and native Ubuntu 24.04 are joint V1 targets. V1 uses Tauri 2 with a shared HTML/JavaScript interface, FastAPI, SQLite, EXL3, GGUF, cloud capability, and three harnesses. WSL is preliminary only and does not substitute for the native Ubuntu proof.
- Operational evolution and engineering evolution are both first-class V1 capabilities. Intelligence-research extension points are architectural seams only; they are not advanced V1 capability or an AGI claim.
- The existing five CAE engines remain Experience, Reflection, Assimilation, Evaluation, and EvolutionController. Parallel, Idle, and Forced are scheduling modes. This direction adds no sixth engine, framework, database, or authority layer. Contract and protocol semantics are in [the CAE contract](CAE_CONTRACT.md); the implemented DTOs in `backend/app/schemas/cae.py` are the machine-authoritative serialized shape. Their current validators check structural constraints only; they do not implement execution, admission authorization, or sandbox attestation. This document intentionally does not duplicate DTO fields.
- Research may propose unconventional or fundamental alternatives. A protected baseline, credible hypothesis, bounded comparative protocol, independent review, resource budget, and owner decision are required before a proposal can replace it; alternatives must not be silently discarded because they are outside a roadmap.
- Stage status: R0 is contract/documentation scope only. The CAE runtime, five-engine execution, sandbox, experiment evidence, and owner acceptance are not established by R0. R1–R8 remain gated as recorded in the CAE contract. Existing app API v1 DTOs, task canonicalization, run behavior, and SSE behavior remain unchanged.

### R0–R8 delivery boundary

R0 freezes terminology, contracts, and scope. R1 establishes authorization fences and arbitrary-code sandbox/compiler-worker controls. R2 establishes native jobs; R3 integrates all three harnesses; R4 integrates memory; R5 supplies both-platform proof including rejection, no-change, interruption, recovery, and owner decision; R6 covers cloud/Fusion and an owner-approved minimal DevHub trial; R7 packages jointly; R8 performs independent final verification on both platform proofs. These are planned gates, not claims of completion. The detailed entry conditions and evidence boundaries are in [the CAE contract](CAE_CONTRACT.md).

## Owner decisions

1. Build the end product, preserving the current frontend rather than creating another disconnected preview.
2. Product root: `X:\DIVINE.POINT\OnePath.Image`. The active frontend is `frontend\index.html`, with assets in `frontend\assets`. Do not recreate the obsolete nested `OnePath.Image\OnePath.Image` layout.
3. Use Python 3.13 for the initial managed application/inference runtime. A separately managed harness may require its own version: the selected Hermes release supports Python 3.14. Keep that environment separate from PyTorch/ExLlamaV3. Do not depend on or modify the customer's global Python, Anaconda, or unrelated environments.
4. EXL3 is the preferred initial local model format. The system must also support other formats through compatible runtime adapters and the frontend Models selection.
5. Keep product-managed runtime installations and dependencies inside the product-managed directory structure, isolated by engine. Keep generated runtime/model/data content out of Git.
6. Ship a paid consumer product with a small online installer as the default and an equivalent terminal setup entry point. Setup automatically downloads and installs the application, managed Python 3.13, selected inference engines and their required dependencies; customers do not install these separately. Offer compatible model downloads during setup or later through Models. An offline bundle is optional, not the default delivery.
7. Provide one Update OnePath action and an equivalent terminal update path. An update applies every component change required for that product release, including managed Python/inference runtimes/dependencies where necessary.
8. Develop and test functionality through VS Code. Reserve the full customer installer/packaged-run acceptance test for the packaging milestone. Development still requires running and testing the real backend and inference engines before claiming they work.

## Consumer purpose and shared companion direction

The owner selected real multi-step tasks with tools, saved results, and recovery
as the first flagship demonstration. OnePath is intended to provide the common
agent/companion service for OnePath, DevHub.Studio, and NextPath.City. The canonical
product-purpose, responsibility boundaries, first cross-app demonstration, and
long-range hypotheses are in [the ecosystem blueprint](ECOSYSTEM_PRODUCT_BLUEPRINT.md).
That design is not a claim that cross-app integration already exists. The
owner-provided Atomic reference is [AtomicBot-ai/atomic-agent](ATOMIC_AGENT_REFERENCE.md),
currently evaluated as a possible harness adapter, not an installed dependency.

## Accepted Memory Fabric direction — 31 October 2026

The owner accepts **OnePath Memory Fabric** as the working architecture and
**TaskContextCompiler** as a major research and engineering component. This
supersedes Microsoft GraphRAG as a required foundation. OnePath owns memory
contracts, provenance, bitemporal behavior, authority, retrieval orchestration,
and controlled evolution. Four logical categories are Working, Episodic,
Semantic, and Procedural. A minimal trusted Memory Integrity Kernel protects
authentication, source provenance, approved-instruction authority, revisions,
deletion, transaction integrity, and version/activation history; it is not a
sixth CAE engine or an OS driver. CAE strategies and generated algorithms cannot
write or bypass those controls. Detailed logical contracts, the bounded SQLite
FTS5 prototype, evidence-completeness rules, research, and acceptance measures
belong in [MEMORY_FOUNDATION.md](MEMORY_FOUNDATION.md).

This is an accepted architecture decision, not an implementation claim. The
current Markdown/Obsidian snapshot importer remains implemented; retrieval,
TaskContextCompiler, and the Integrity Kernel remain planned. SQLite FTS5 is the
bounded recall baseline; current recorded Windows SQLite 3.53.1 build support
does not prove retrieval behavior, Ubuntu support, or performance. Preserve
useful Obsidian and Graphify workflows; GraphRAG and Graphiti are optional
alternatives, not required dependencies. Atlas is a permission-filtered
presentation of canonical records, not another memory store. The five CAE
engines and three scheduling modes remain unchanged; [CAE R0–R8 gates](CAE_CONTRACT.md)
continue to govern authority, isolation, and promotion.

## Observed repository state

The initial read-only inspection on 21 October 2026, before guided preparation, found:

| Path | Observed state |
| --- | --- |
| `frontend/index.html`, `frontend/assets/` | Present; active frontend entry and assets |
| `docs/MODEL_FRONTEND_CONTRACT.md` | Present; model configuration/event interface |
| `backend/app/` | Empty scaffolding: agents, api, providers, schemas, services, storage, tools |
| `backend/migrations/`, `backend/tests/` | Present, with no files found |
| `runtime-manifests/` | Present, empty |
| `.local/` | Present, empty |
| `.gitignore` | Absent at inspection; no matching `.local` exclusion |
| `to-delete/` | Historical content; no deletion authorized |

These observations may become stale. Empty directories do not appear in Git status, and clean status does not prove generated content is ignored. Verify `.local/` exclusion before placing generated files there. No backend execution, model installation, installer, or updater has been verified by this inspection.

## Current implementation and runtime status

### Local owner authentication interface — 31 October 2026

The local pairing/session interface and operator limits are documented in [LOCAL_AUTH.md](LOCAL_AUTH.md). Current source wires the API router/default-deny middleware, initializes auth before provider setup, and enforces the narrow restart-quarantine exceptions. The frontend session client and pairing hook exist in source but actual UI mount/focus/browser acceptance remains unverified. Independent API review and local tests support bounded source behavior, not live-server, native POSIX, sandbox, packaged, or owner acceptance. The CLI's current `expiresAt` parser also requires `Z`, while the interface permits both `Z` and `+00:00`; retain that mismatch as an open correction.

### Latest bounded evidence — 29 October 2026

This block supersedes older present-tense acceptance statements below. The known
EXL3 backend sequence passed model load, response, cancellation and explicit
recovery under its recorded scope. A later fresh isolated headless browser batch
passed wide/narrow Workspace controls, Output/history/Focus/Team, browser-local
draft persistence and unchanged fixture task identities. Navi's earlier false
visibility failure came from an animation-dependent test selector. Actual live
UI Stop/model reload/recovery remains pending; no whole-app completion is claimed.

The optional [shared local status feature](SHARED_LOCAL_CONNECTIONS.md) passed
focused CPU/syntax checks, live read-only route checks and browser presentation.
It can report an explicitly configured llama.cpp server without submitting work
or controlling it; it does not connect chat or a harness. The owner has since
stopped their independent llama.cpp/Atomic services to free resources. Detailed
attempts and evidence remain in the active structure-foundation mission; temporary
developer services and receipts do not establish packaged installation behavior.

The application and TabbyAPI provider completed a bounded EXL3 chat milestone, with an independent final review passing its scoped chat, readiness, shared-history, compaction, cancellation/count, and demo-label criteria. Earlier evidence includes two persisted Gandalf turns, SSE delivery, matching 123-token counts across the application/provider/engine, a synthetic summarizer probe, and an 80,249-token recall proof on the unchanged native profile. This milestone is not final product acceptance. The [provider documentation](TABBY_HTTP_PROVIDER.md) and [chat contract](CHAT_BACKEND_CONTRACT.md) describe the implementation boundaries; the mission checkpoint at `C:\Users\musan\.codex\company\missions\onepath-image\exl3-provider-integration-20261023\MISSION.md` records detailed proof status.

### 24 October 2026 late-evening EXL3 milestone evidence

Correction 12's 52 focused automated backend checks passed with stable machine-recorded artifact hashes: 24 provider, 7 compaction, and 21 run/API checks. Most use simulated engine responses; they are separate from real EXL3 checks. The bounded milestone also passed actual local EXL3 compaction, summary provenance, canonical history, recall/recount, and streamed cancellation: a first visible response piece arrived while the run was still running, followed by HTTP 202 with `cancellationRequested=true`, terminal `cancelled` with no later text, a successful follow-up, and an exact 2,135-token count. The final review independently passed the scoped EXL3 chat, readiness, shared-history, compaction/count, cancellation, and demo-health-label criteria. This proves application/provider behavior; no direct GPU-job cancellation acknowledgement is exposed. EXL3 restore 9 passed with the 81,920-token Q8 profile.

### Historical implementation and acceptance snapshot — 28 October 2026

Source/CPU checks have passed for the shared four-persona conversation foundation, Workspace tasks, archive controls, Markdown snapshots, and signed-release planner. Historical bounded runtime/UI evidence is limited to the separately recorded Models lifecycle trials; the new features have no live acceptance yet. The conversation layer maintains separate histories and drafts with server role instructions; tools, specialized harnesses, and delegation are not connected. Durable Workspace tasks use the run API's idempotency, state, and cancel paths rather than demo timers. Workspace source adapts output/history, Focus, global F, and known public Team runs; three source-review defects were corrected by Root. See [conversation source](AGENT_CONVERSATIONS.md) and [Workspace foundation](WORKSPACE_FOUNDATION.md).

Chat/project archive, restore, and delete controls are implemented in source with named delete confirmation and a server active-run guard; project deletion detaches chats while preserving tasks/messages. Twenty-four temporary-database tests, three client fixture files, and five JavaScript syntax checks passed; no owner records were deleted. Local Markdown/Obsidian snapshot import has source checks for hashes, provenance, and limits (five temporary-database tests and two JavaScript syntax checks); there is no live vault watcher. See [project/archive behavior](PROJECTS_AND_ARCHIVE.md) and [memory foundation](MEMORY_FOUNDATION.md). GraphRAG has not been indexed, queried, connected, or injected into chat context.

The current `installer/ReleasePlan.psm1` and `setup.ps1` provide offline signed-release verification and dependency/update planning only; 12 stock Windows PowerShell fixtures passed. `applySupported=false`: there is no download, install, activation, repair, or rollback path, production key/release configuration, or customer-installer readiness. See [installer plan](../installer/README.md).

For Models, owner folder selection works; of four tested model options, only `Qwen3.8-27B-exl3-2.50bpw` loaded. The other three failed and were deferred under the owner's structure-first direction. Preserve the earlier EXL3/GGUF 8k proofs as historical evidence; effective GGUF quantization and layer values remain unreported. Do not claim 80k managed fit or a 4-GiB cache allocation.

Live visual/runtime acceptance remains unverified: the browser attempt at port 8091 returned `ERR_CONNECTION_REFUSED`. The final Models report remains **OVERALL FAIL / PENDING** solely because duplicate-root confirmation could not be completed due to Sky geometry/focus tooling; no application defect was proven, and the GUI must not be retried. The report records the new empty-root and native-cancel flows, two roots persisted across restart, and completed flows retaining revision 1, without claiming post-duplicate behavior. See [managed behavior evidence](../.local/verification/gguf-managed-behaviors2.json) and the [final verification report](../.local/verification/models-final-verification3.md). Full product acceptance, portable Python, release licensing, GraphRAG, and harnesses remain unfinished. Existing owner processes were not controlled; four isolated previews and trial engines were cleaned. No full-app 10/10 claim is made.

## Current application foundation and proposed components

FastAPI and Uvicorn are adopted in the development source. The application stays independent of inference libraries. Persisted application chat, the TabbyAPI provider adapter, and EXL3 generation have historical real-inference evidence; that evidence does not mean services are currently running or establishes final product acceptance. Broader runtime installation/management beyond the verified development registration and UI lifecycle checks, broader model control, and other integrations remain proposed or unaccepted until separately verified.

The development backend source provides versioned application health, explicit frontend/static routes, read-only NVIDIA NVML telemetry, a bounded read-only local model file inventory, and the implemented optional context-budget setting. The local inventory reads only server-configured roots from `ONEPATH_LOCAL_MODEL_ROOTS`; it does not accept client paths, validate inference compatibility, load models, or choose a runtime. The historical TabbyAPI integration is documented in [TABBY_HTTP_PROVIDER.md](TABBY_HTTP_PROVIDER.md). Model loading and selection remain outside the existing inventory; see the dated runtime evidence above and [MODEL_FRONTEND_CONTRACT.md](MODEL_FRONTEND_CONTRACT.md) for current frontend/API boundaries. See [GPU_FRONTEND.md](GPU_FRONTEND.md) for telemetry evidence and limits.

```text
Existing web frontend
    -> application API and authenticated/local-session boundary
        -> conversations, tasks, profiles, approvals and artifact storage
        -> server-side routing and permissions
        -> agent/harness adapters
        -> model catalog and runtime/resource manager
            -> TabbyAPI / ExLlamaV3 adapter: EXL3 first
            -> llama.cpp server adapter: GGUF next
            -> additional engines when a concrete requirement justifies them
        -> controlled tool execution workers
```

Provider connectors, inference runtimes, models, personas, and agent harnesses are different components. An agent persona is not bound to one weight format. Similar HTTP chat APIs do not guarantee equivalent tool parsing, loading, unloading, cancellation, or context management.

SQLite persistence has historical real-chat evidence for single-machine development. SQLAlchemy/Alembic remain proposed unless separately verified. Hosted/multi-user deployment and any PostgreSQL transition require a separate decision and verified owner-derived authorization. Do not mistake a local single-user database for production tenant isolation.

## Model and runtime contracts

- Catalog each model variant with a stable ID, source/revision, architecture, weight container/format, quantization, tokenizer/chat template, path or connection, license metadata, compatible engine/version, and validated capabilities.
- Separate estimated memory requirements from observed memory use. A filename or a Safetensors container alone is not proof of compatibility.
- Expose actual format, quantization, engine, availability, and capability information in Models. Normally resolve the compatible enabled engine automatically; allow an advanced override only among compatible choices.
- Define a common generation/stream/cancellation contract and a separate capability-aware runtime-management contract. Unsupported operations must be explicit.
- Coordinate active runs, model load/unload, GPU allocation, context/cache budgets, queueing, timeouts and recovery centrally. Begin with one GPU-resident generation model unless measurements justify more.
- Do not unload a model serving active work silently. Handle cancellation or waiting explicitly before switching engines/models.
- Prove extensibility with real EXL3 and GGUF generation through the same frontend before claiming multi-format acceptance. Additional formats require their own compatibility and behavior evidence.

Reference machine reported in the guided chat: RTX 5080 with approximately 16 GB VRAM, 31.7 GB RAM, driver 617.42. These are reported reference facts, not universal minimum requirements or a guarantee that any particular model fits. Runtime package pins belong in versioned manifests after compatibility checks and execution tests, not in permanent prose copied from an old chat.

## Product layout

Existing directories remain intact. Create additional paths only when their milestone requires them.

```text
OnePath.Image/
  frontend/                         existing UI
  backend/
    app/
      api/ schemas/ services/       application interfaces and behavior
      providers/ agents/ tools/    connectors, harnesses and tools
      storage/                     persistence
      runtimes/                    proposed runtime adapters, when implemented
    migrations/ tests/
  runtime-manifests/                tracked compatibility/provisioning definitions
  installer/                        proposed packaging/bootstrap/update logic
  docs/
  .local/                           generated, Git-ignored development content
    runtimes/                       isolated engine environments/binaries
    models/ data/ logs/             generated/private content
  to-delete/                        preserve unless owner authorizes action
```

Do not introduce a separate root `runtime-adapters` directory: adapter code belongs with the backend. Do not commit virtual environments, binaries, models, credentials, private databases or logs. Installed distribution layout need not reproduce the source checkout; the installer must manage its own writable locations and component inventory. Product-managed Python can be bundled/provisioned there independently of global Python. A virtual environment tied to a developer's interpreter is not by itself a portable distribution.

Worktrees are optional development isolation, not product dependencies. Preserve current owner work; a new worktree starts from committed state and does not automatically contain uncommitted frontend changes. Avoid duplicating large runtime/model stores across worktrees.

## Agent and frontend integration

All four personas belong in the complete product plan:

| Persona | Intended function | Harness status |
| --- | --- | --- |
| Gandalf | Planning, bounded delegation and synthesis | Shared conversation source implemented; tools and delegation are not connected |
| Hermes | Research and exploration | Actual NousResearch/hermes-agent selected; runs-API adapter has six fixture tests, actual runtime/tools/UI not yet accepted |
| Atomic | Repeatable workflows and connected tools | Actual Atomic0.6.12 + known local EXL3 completed two scoped reads and saved a verified result in a private backend trial; consumer routing/cancellation integration unfinished |
| DeepSeek | Technical/code assistance | Actual official deepseek-ai/deepseek-harness selected for SDK evaluation; controlled profile and adapter remain unfinished; DeepSeek model/API is a separate component |

Shared four-persona conversation source is implemented with separate histories, drafts, and server role instructions; the public personas still do not advertise connected tools, specialized harnesses, or delegation. Private runtime proof and transport code do not establish consumer readiness. See [Atomic's verified boundary](ATOMIC_HARNESS_BOUNDARY.md) and [real Hermes/DeepSeek integration](HERMES_DEEPSEEK_INTEGRATION.md) for current evidence and remaining work. A role prompt alone does not prove research, tool execution, or workflow capability. Generated code from Gandalf is development assistance, separate from the consumer DeepSeek persona.

Preserve the existing frontend events and honest status meanings in the model contract. Workspace tasks share durable run state, idempotency, and cancellation, with Output/history, Focus, and public Team activity. The latest bounded browser batch passed the reading/control/persistence surface described above. Actual live UI Stop/model recovery, model downloads, and complete customer job/progress flows remain separate acceptance gates. Earlier connection-refused evidence is historical.

Conversation continuity across Home and Workspace is implemented in source with separate histories and drafts plus server role instructions. Chat/project archive, restore, and delete controls use confirmation and an active-run guard; project deletion detaches chats and preserves tasks/messages. The controls passed 24 temporary-database tests plus client fixtures and syntax checks. Owner records were not deleted; see [project/archive behavior](PROJECTS_AND_ARCHIVE.md).

Enforce Local/Cloud/Fusion decisions server-side, including harness auxiliary models and fallback. Local inference and internet tool access are separate permissions. The owner approved removing Gandalf's cloud-only restriction on 22 October 2026. The frontend makes every persona locally configurable using shared system routing and agent preferences; Local mode blocks cloud for every persona. Cloud/Fusion routing, specialized harnesses, tools, and delegation remain unconnected and unaccepted.

Tool execution needs scoped task workspaces, validated arguments, approval for consequential actions, time/output limits, artifact ownership and duplicate-action protection. Persist interrupted states; never automatically replay uncertain external actions after a restart. Offer pause/resume only where the adapter supports it safely.

## Installer and terminal setup

The owner approved the online installer approach on 21 October 2026. One small setup program downloads the required product payloads automatically rather than embedding every runtime dependency in the initial executable. The terminal setup entry point uses the same provisioning logic and component manifest. An offline distribution may optionally package those payloads for customers without installation-time internet access.

Current implementation is plan-only: `installer/ReleasePlan.psm1` and `setup.ps1` verify offline signed-release metadata and produce a dependency/update plan. Twelve stock Windows PowerShell fixtures passed. `applySupported=false`; there is no download, installation, activation, repair, rollback, production key/release configuration, or customer-installer readiness. The verifier accepts RSA keys from 3072 through 8192 bits, requires a separately trusted public key and static artifact-host allowlist; DNS/redirect policy remains future work. See [installer plan](../installer/README.md).

Keep four quantities distinct: initial installer size, application payload size, Python/engine/dependency download size, and model weight size. Moving payloads to online downloads reduces the initial installer size, not the total network traffic or installed disk requirement. Final sizes must be measured from selected release artifacts; earlier chat size ranges were planning estimates, not acceptance criteria.

Both entry points should invoke the same versioned provisioning plan:

1. Check supported OS/architecture, hardware, driver and writable disk capacity.
2. Establish the product installation and isolated managed Python 3.13/engine environments.
3. Show the required downloads and disk requirements, obtain pinned application/Python/engine/dependency artifacts, verify integrity/authenticity, and support download resumption and actionable retry errors.
4. Automatically install the components needed for selected features and offer compatible model choices with sizes/license information. Model downloads may occur during setup or later; local inference readiness requires an actual usable model, so an installation with no model must say so honestly.
5. Configure local service authentication, storage and launch behavior without changing unrelated environments.
6. Run health checks and a bounded actual inference check before declaring local AI ready.
7. Register the launcher, component inventory and update/repair/uninstall behavior.

The customer opens OnePath without manually starting inference servers. Use released binaries/wheels where viable; do not assume customers have Git, VS Code, compilers, Conda or Python. Driver updates, unsupported hardware and any elevation/reboot requirement need explicit handling; setup cannot guarantee every model works on every machine. Terminal setup must expose verification/errors rather than blindly executing mutable remote content.

Existing upstream references list ExLlamaV3 as MIT and TabbyAPI as AGPL-3.0. Current paid packaging, source/notice obligations, model licenses and component redistribution still need review before final release selection. A separate process or a download step does not automatically settle licensing obligations. See [ExLlamaV3 license](https://github.com/turboderp-org/exllamav3/blob/master/LICENSE) and [TabbyAPI license](https://github.com/theroyallab/tabbyAPI/blob/main/LICENSE).

## Unified update design

One Update OnePath action covers the application and every required installed component change for a compatible product release. Provide an equivalent terminal update entry point. It must not independently upgrade every package to whatever is newest.

- Use signed release manifests containing product/component versions, pinned artifact locations/hashes, compatibility constraints, migration requirements and required disk capacity. Validate against a trusted signing identity, not just a checksum downloaded beside an artifact.
- Update only components needed by installed/selected features. Preserve model weights unless an explicit or informed compatibility-driven model update is required.
- Stage and verify downloads before switching a working installation. Show affected components, download sizes and restart requirements.
- Drain or safely stop active work under user control. Do not silently interrupt generation or consequential tool execution.
- Back up data before relevant migrations. Activate a complete compatible component set and run health checks; retain a prior working runtime until the new one passes.
- Roll back component activation on failure where safe. Database migration/data recovery needs explicit handling; do not promise universal rollback.
- Preserve conversations, credentials, preferences and user artifacts. Repair and uninstall use the same inventory, with explicit choices about user-data/model retention.

## Development and acceptance sequence

1. Protect generated local content with the narrow Git exclusion; record runtime compatibility and setup manifests.
2. Establish managed Python 3.13 and one isolated EXL3 runtime, then verify actual inference through VS Code.
3. Build the application API and frontend adapter, real connection checks, shared persona chat, streaming and cancellation.
4. Add persistence, real model management and a second GGUF runtime; verify selection and safe switching.
5. Add secure cloud/Fusion routing and specialized agent execution, tools, approvals and real Workspace tasks.
6. Develop setup/update/repair logic as product functionality alongside modules, with narrow tests and staged-update simulations where appropriate.
7. Run the complete functional/security/recovery/browser checks in the VS Code development workflow.
8. At packaging, build the installer and terminal distribution; test clean-machine installation, packaged first launch/inference, update/failure recovery, repair and uninstall. The owner tests customer installation at this final packaging milestone rather than reinstalling the product repeatedly during development.

Acceptance must distinguish source checks, development runtime integration, packaged behavior and owner acceptance. Required final evidence includes real EXL3/GGUF inference, all four supported personas/harness behaviors, protected Local/Cloud/Fusion routing, real cancellation and load/unload, durable task/history recovery, permission boundaries, and installer/updater lifecycle results. Document unavailable coverage and failures; no demo or artifact download alone proves execution.

## Unresolved decisions and limits

- FastAPI and Uvicorn are adopted for the development API. SQLAlchemy/Alembic, actual harness selection and release packaging technology remain proposals until adopted.
- Exact engine/model versions, dependency locks, compatibility and performance require current artifact checks followed by execution tests. Python 3.13 is selected; a particular package combination is not yet runtime-accepted.
- Final product minimum hardware, supported operating systems and first bundled/downloadable model are not selected.
- Shared conversation behavior requires an explicit product decision. Gandalf's cloud-only restriction was removed with owner approval; backend routing enforcement remains pending.
- Installer/updater implementation, GGUF inference acceptance, Hermes/Atomic/DeepSeek harness behavior, cloud/Fusion routing, release/license handling, and packaged acceptance remain unfinished.
- The planned 4-GiB cache ceiling is not proof that 4 GiB of cache bytes are allocated or available.
- Approved future original OnePath code intelligence: parse supported source languages into symbols, imports, calls, and a dependency graph; provide bounded search, context assembly, traces, and change-impact views with source locations and index-freshness status. Implement this originally with commercially compatible dependencies. GitNexus is a reference only, not an approved dependency or source donor; do not copy restricted code/assets or mechanically rewrite them. Graphify is a candidate/reference, not a mandatory installation.
- Local Markdown/Obsidian snapshot import is implemented in source with hash/provenance/limit checks; five temporary-database tests and two JavaScript syntax checks passed. There is no live vault watcher or connected retrieval path. The prior Microsoft GraphRAG selection is superseded: GraphRAG is optional and remains uninstalled/unconnected; its `not_connected` status is a legacy API placeholder, not an architectural mandate. Windows SQLite 3.53.1 FTS5 build support is recorded, but retrieval, native Ubuntu behavior, and performance remain unverified. See [memory foundation](MEMORY_FOUNDATION.md).
- Graphify is an optional structural code/technical map; do not re-index the same corpus on every query. Its export commands are not yet reliable at the current version, so do not copy its installation guide. See [Graphify](https://github.com/Graphify-Labs/graphify). TypeSafe Jev endpoint: `https://api.typesafe.ai/v1/systemone` (see [quickstart](https://docs.typesafe.ai/introduction/quickstart)); it is optional for Cloud/Fusion routing only and never required for local/offline operation. Bounded choices may misclassify. No cloud request or integration has occurred.
- Embeddings, learned ranking, and external retrieval engines remain optional candidates; no memory dependency or speedup claim is selected. Measure candidate recall, final-context completeness, downstream task success, actual token cost, latency, compute, provenance, security, and deletion behavior separately. Do not claim paper benchmark results as OnePath results or promise zero hallucinations or a fixed latency.
- Hosted service/local-companion security needs dedicated decisions before release.
- No installer, updater, GGUF, GraphRAG, harness, cloud/Fusion, or full QA acceptance is claimed. Dated restore/integration results do not establish continuous live health.

## Maintaining this record

Update this document when the owner changes a decision or implementation evidence supersedes a proposal. Keep package pins in manifests, interface details in the frontend/API contracts, and test results in their reports rather than duplicating them here. Reconcile guidance given to Guide Gandalf with this document; correct stale instructions before the owner uses them. Never claim this document is automatically enforced or always recalled: retrieve it before related architectural guidance.
