# OnePath Memory Fabric foundation

Updated: 31 October 2026, Asia/Kuala_Lumpur.

**Owner-approved direction:** OnePath Memory Fabric is the working memory
architecture; TaskContextCompiler is a major research and engineering component.
This supersedes GraphRAG as the required foundation. GraphRAG and Graphiti remain
optional candidates. This document owns R4 memory semantics, prototype scope,
and evaluation. The CAE contract remains authoritative for CAE authority,
isolation, R4/R5 gates, and promotion.

**Current implementation:** explicit Markdown/Obsidian snapshot import and its
HTTP endpoints below exist in source. Retrieval, cross-session recall through
FTS5, TaskContextCompiler, and Memory Integrity Kernel are planned, not
implemented or accepted. The API's GraphRAG `not_connected` value is a legacy
implementation placeholder; it does not select the architecture. A recorded
Windows SQLite 3.53.1 build exposes `ENABLE_FTS5`; this confirms build support
only. Retrieval correctness, native Ubuntu behavior, and performance are not
verified. Imported content is not automatically injected into chat.

Workspace → Memory accepts an explicitly selected Markdown folder, including an
Obsidian vault snapshot. Import is a separate user action after folder selection.
The browser reads only `.md` files outside hidden folders and sends the selected
content to the same-origin local application. It does not send a machine path,
execute Markdown, modify the vault, install Obsidian or contact a cloud service.
This is a snapshot import, not a live vault watcher. Changed imports create distinct
snapshots; importing the same title/document set twice reuses the same snapshot.

Bounds: 1000 documents per snapshot, 256 KiB UTF-8 text per document, 8 MiB total
text, 12 MiB serialized request, 64 snapshots and 64 MiB total stored text. Hidden,
absolute, traversing, non-Markdown and case-colliding paths are rejected. Limits
reject an import atomically; existing snapshots stay unchanged. The app never opens
a submitted path on the server filesystem. Text is stored as untrusted source data,
not instructions or tool requests.

SQLite holds snapshot identity, source title, relative document paths, content hashes,
stable document IDs and original text. Snapshot hashes cover sorted path/hash pairs
and the title. Document IDs bind snapshot, relative path and content hash. Metadata
queries page 100 records without returning document text. These are source
provenance records, not final compiled-context evidence or a connected retrieval
index. Documents and snapshots are independent of chat/project deletion in this
slice.

## HTTP

- `GET /api/v1/memory/sources`: bounded snapshots plus explicit GraphRAG
  `not_connected` status.
- `POST /api/v1/memory/sources`: `{title, documents:[{relativePath,content}]}`.
  Same-origin loopback only, strict fields, bounded streaming body. Identical
  snapshots return 200; newly saved snapshots return 201.
- `GET /api/v1/memory/sources/{id}/documents?before=…`: paged provenance metadata.

The existing `not_connected` retrieval status is preserved as current API behavior.
There is no connected index/query execution, automatic filesystem harvest,
background indexing, embedding request, or hidden online fallback in the current
implementation. The planned SQLite FTS5 recall baseline will be a genuine local
query path, not represented as a connected GraphRAG service.

## R4 logical memory contracts — planned

These versioned names describe logical semantics only; they are not existing DTO
classes, production JSON shapes, or runtime evidence.

### Records, authority, and time

Use four logical categories: **Working**, **Episodic**, **Semantic**, and
**Procedural**. Record kinds include evidence, claim, inferred relation, and
approved instruction. A `MemoryRecordV1` describes identity/revision, category
and kind, trusted owner/workspace/agent/source scope, provenance hash and source
position, valid-time interval (known or unknown) and record-time, supersession
and conflicts, approval authority, retention, and deletion state. `asOf` queries
must distinguish current from historical meaning; newer record-time alone does
not make a claim more authoritative.

Each request binds a verified principal, mission/workspace/source scope, as-of
time, source/index/policy revisions, and finite candidate/token/resource budgets.
Caller text cannot mint a principal, widen scope, or grant access. A
`CandidateSetV1` contains only authorized candidate references/spans, source and
index revisions, provenance, retrieval scores, declared dependencies, and cost
metadata; unauthorized IDs, titles, and counts must not leak.

A `CompiledContextV1` contains ordered validated evidence references, source
provenance, current instructions, mandatory qualifications, dependency coverage,
conflict disclosure, measured token count and its tokenizer/model reference,
policy version, snapshot revision, and a status such as `READY`,
`INSUFFICIENT_EVIDENCE`, `BUDGET_INFEASIBLE`, `STALE`, or `DENIED`. These statuses
are proposed logical values, not current API fields. Evidence-complete means
complete under the declared requirements, scope, and snapshot—not complete
knowledge of the world. Permissions, authenticated current instructions,
dependencies, and mandatory qualifications can never be traded away for utility.
Unresolved conflicts must be disclosed. If a verified tokenizer is unavailable,
do not claim a token bound or automatically inject compiled context; basic
authorized retrieval may still return candidates.

An `OperationTraceV1` separates candidate-pool construction, selection, compiled
context, downstream outputs, and outcomes. It records cost, token, resource,
provenance, temporal, and security measurements without dumping raw private
transcripts. Evaluation authority and protected policies are not writable by CAE
strategies or generated algorithms.

### Memory Integrity Kernel boundary

The Memory Integrity Kernel is a minimal trusted OnePath service boundary, not an
OS driver, new database, or sixth CAE engine. It owns authentication and
capability/scope checks; source-provenance and approved-instruction validation
(authenticated origin, not quoted `OWNER` text); revision/CAS, idempotency,
nonce, and transaction ownership; canonical write/correction/delete authority;
active-version, retention, and audit state. CAE strategies and executable
algorithms receive approved read-only inputs and return validated outputs; they
cannot directly write canonical memory, permissions, approvals, or evaluator
policy. This boundary does not prove OS isolation: R1 sandbox controls remain
mandatory before untrusted execution.

Components that currently commit independently do not establish one shared
atomic transaction. Design an explicitly owned SQLite unit of work; do not assume
an existing auth/write helper preserves an outer transaction. Deletion must
invalidate derived indexes, contexts, caches, and jobs, with backup retention
following the approved deletion policy. Do not promise that every historical
copy disappears immediately. Version control means artifact/revision/activation
history; a Git commit is provenance, not deployment authority. Configuration
activation uses the existing CAS/nonce/revision contract. Executable activation
still needs a trusted staged-release journal and rollback gate; SQLite cannot
make database and filesystem deployment atomic. Only trusted owner-reviewed
maintenance outside CAE autonomy may modify trust controls.

## R4 prototype and progression — planned

Start with one owner, one workspace, and an explicitly authorized existing
Markdown snapshot. Build a bounded SQLite FTS5 candidate-recall baseline with
deterministic budget assembly and citations. Do not require embeddings, rankers,
graphs, or LLM-based selection for this baseline. Prove useful cross-session and
restart recall, while retaining current owner instructions. Snapshot selection
is not an access grant; do not add automatic filesystem harvesting. Set finite,
caller-approved limits before implementation and enforce actual token admission
through existing provider/resource controls; make no hardware-fit claim.

R4 advances in order: (1) records and authority; (2) useful functional recall;
(3) hybrid retrieval and context compilation; (4) adapter/strategy comparison;
(5) permission-filtered, real-data Atlas presentation. Do not postpone recall
while researching more advanced methods. Atlas projects authorized views of
canonical IDs and records; it is presentation, not another memory store. Human
and agent consumers see the same permission-filtered canonical identities.

R5 retains its existing joint Windows and native Ubuntu 24.04 evidence gate:
rejection, no-change, interruption, recovery, and owner decision. WSL is
preliminary only. R0/R4 design approval does not satisfy R5. The currently
recorded Windows FTS5 build support does not prove the query path, native Ubuntu
behavior, performance, or release readiness.

## Research candidates and evaluation — planned

[AgentMemRec / Agent Memory as Recommendation (arXiv:2610.34673)](https://arxiv.org/html/2610.34673#S4)
is an optional algorithm candidate, not a required dependency or OnePath
performance result. Its query path retrieves with BM25 and dense search, fuses
ranks with RRF, applies a LightGBM/LambdaRank ranker with a cross-encoder score
feature, then greedily fills the token budget ([retrieval and ranking](https://arxiv.org/html/2610.34673#S3.4),
[budgeting and evaluation](https://arxiv.org/html/2610.34673#S5)). The default
query path makes zero generative calls; dense search, cross-encoder scoring,
ranking, and ranker training still have compute and latency costs. Optional
write-time LLM use is separate. Itemwise relevance and composition assumptions do
not establish dependency closure. Its main multi-session comparison excludes
unanswerable questions, so OnePath must separately gate protected abstention and
dependency/qualification completeness. These methods do not require OnePath to
adopt LightGBM, a cross-encoder, or ranker training.

[MEMO (arXiv:2609.07471)](https://arxiv.org/html/2609.07471#S3) studies
query-conditioned source-unit/span extraction and a trained presentation manager
using counterfactual reader utility ([utility details](https://arxiv.org/html/2609.07471#S3.3)).
This is a context-presentation method, not trusted authority or access enforcement.
Both papers are experimental candidates; their reported scores and costs are not
OnePath results and do not prescribe dependencies or installations.

For any later adapter/strategy comparison, hold the candidate pool and reader
token budget fixed. Compare greedy budget fill with dependency-aware selection,
include dependency ablations and an end-to-end baseline, and keep development
cases separate from hash-protected held-out cases. Do not train on held-out
cases or let a candidate grade itself.

Evaluate separately: **candidate recall**, **final declared-context
completeness**, and **downstream task success**. Track exact sources and
provenance; permission/security and temporal correctness; mandatory
qualifications and abstention; index/write/query/training costs when applicable;
p50/p95 latency; actual prompt tokens; RAM/VRAM; recovery; and deletion. Answer
correctness alone does not prove context completeness. No resource or authority
tradeoff is allowed. Independent review and explicit owner activation are
required before promotion. The frozen CAE E1 and operational retrieval pilot
remain in `CAE_CONTRACT.md`; the pilot's measured-token threshold is a separate
gate, not a substitute for these correctness measures.
