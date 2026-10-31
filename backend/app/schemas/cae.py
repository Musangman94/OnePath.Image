"""Strict v1/v2 CAE records. These validate descriptions; they grant no execution."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)
_SHA256 = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_GIT_REVISION = re.compile(r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_WINDOWS_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


ArtifactRole = Literal[
    "declarative.skill", "declarative.instruction", "declarative.strategy",
    "declarative.retrieval", "declarative.routing", "declarative.workflow",
    "agent.configuration", "algorithm.module", "executable.package", "source.patch",
    "architecture.variant", "model.experiment",
]
EvolutionType = Literal["operational", "engineering", "intelligence_research"]
PromotionAuthority = Literal[
    "operational_configuration", "nontrusted_code", "trusted_maintenance", "research_only",
]
EvolutionDimension = Literal["efficiency", "reliability", "capability", "research_productivity"]
ResearchCapability = Literal[
    "read_scoped_source", "compile_isolated_candidate", "execute_isolated_candidate",
    "local_model_inference", "cloud_model_inference",
]


class ArtifactRef(_DTO):
    id: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    sha256: StrictStr = Field(pattern=_SHA256.pattern)
    version: StrictInt | None = Field(default=None, ge=1)


class EvolutionTargetV2(_DTO):
    evolutionType: EvolutionType
    targetKind: ArtifactRole
    payloadSchemaRef: ArtifactRef
    intendedPromotionAuthority: PromotionAuthority
    dimensions: list[EvolutionDimension] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_dimensions(self):
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions must be unique")
        return self


class CandidateManifestV2(_DTO):
    schemaVersion: Literal[2] = 2
    candidateId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    scopeRef: ArtifactRef
    target: EvolutionTargetV2
    baselineRef: ArtifactRef
    parentVersionRef: ArtifactRef | None
    payloadRef: ArtifactRef
    sourceStateRef: ArtifactRef
    environmentRef: ArtifactRef
    buildRecipeRef: ArtifactRef
    evaluationProtocolRef: ArtifactRef
    resourceProfileRef: ArtifactRef
    provenanceRefs: list[ArtifactRef] = Field(min_length=1)
    dependencyRefs: list[ArtifactRef] = Field(default_factory=list)
    requestedCapabilities: list[ResearchCapability] = Field(default_factory=list)
    rollbackRef: ArtifactRef | None

    @model_validator(mode="after")
    def unique_capabilities(self):
        if len(self.requestedCapabilities) != len(set(self.requestedCapabilities)):
            raise ValueError("requestedCapabilities must be unique")
        return self


class ResearchBudgetV1(_DTO):
    schemaVersion: Literal[1] = 1
    maxWallTimeMs: StrictInt = Field(ge=1, le=86_400_000)
    maxModelTokens: StrictInt = Field(ge=1, le=10_000_000)
    maxOutputTokens: StrictInt = Field(ge=1, le=1_000_000)
    maxSourceBytes: StrictInt = Field(ge=1, le=1_073_741_824)
    maxSandboxLaunches: StrictInt = Field(ge=0, le=100_000)
    maxScratchBytes: StrictInt = Field(ge=1, le=10_737_418_240)
    maxOutputBytes: StrictInt = Field(ge=1, le=1_073_741_824)
    maxCloudCostMicrounits: StrictInt = Field(default=0, ge=0, le=1_000_000_000)


class ResearchAuthorizationV1(_DTO):
    schemaVersion: Literal[1] = 1
    authorizationId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    ownerPrincipalId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    scopeRef: ArtifactRef
    issuedAt: datetime
    expiresAt: datetime
    allowedEvolutionTypes: list[Literal["operational", "engineering"]] = Field(min_length=1)
    allowedTargetKinds: list[ArtifactRole] = Field(min_length=1)
    allowedCapabilities: list[ResearchCapability] = Field(default_factory=list)
    allowedModelRefs: list[ArtifactRef] = Field(default_factory=list)
    allowedBuildRecipeRefs: list[ArtifactRef] = Field(default_factory=list)
    allowedEnvironmentRefs: list[ArtifactRef] = Field(default_factory=list)
    allowedSourceStateRefs: list[ArtifactRef] = Field(default_factory=list)
    budgetsRef: ArtifactRef | None = None
    boundedBudget: ResearchBudgetV1 | None = None
    cloudConsentRef: ArtifactRef | None = None
    productionPromotionAuthorized: StrictBool = False

    @model_validator(mode="after")
    def validate_authorization_record(self):
        for value in (self.issuedAt, self.expiresAt):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("authorization timestamps must be UTC")
        if self.expiresAt <= self.issuedAt:
            raise ValueError("expiresAt must be after issuedAt")
        if self.productionPromotionAuthorized is not False:
            raise ValueError("production promotion cannot be authorized by a research record")
        if (self.budgetsRef is None) == (self.boundedBudget is None):
            raise ValueError("provide exactly one of budgetsRef or boundedBudget")
        if self.boundedBudget is not None and self.boundedBudget.maxCloudCostMicrounits > 0 and self.cloudConsentRef is None:
            raise ValueError("positive cloud budget requires explicit cloudConsentRef")
        for name in ("allowedEvolutionTypes", "allowedTargetKinds", "allowedCapabilities"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        return self


class SourceFileEntryV1(_DTO):
    path: StrictStr = Field(min_length=1, max_length=1024)
    sizeBytes: StrictInt = Field(ge=0)
    sha256: StrictStr = Field(pattern=_SHA256.pattern)
    origin: Literal["tracked", "untracked", "generated"]

    @model_validator(mode="after")
    def normalized_relative_path(self):
        path = self.path
        if (path.startswith("/") or "\\" in path or ":" in path or _CONTROL.search(path)
                or path.endswith("/")):
            raise ValueError("path must be a normalized relative POSIX path")
        parts = path.split("/")
        if any(not part or part in {".", ".."}
               or part.endswith((".", " ")) or _RESERVED_WINDOWS_NAME.fullmatch(part)
               for part in parts):
            raise ValueError("path contains an invalid, hidden, traversing, or reserved component")
        return self


class SourceStateManifestV1(_DTO):
    schemaVersion: Literal[1] = 1
    sourceStateId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    repositoryId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    scopeRef: ArtifactRef
    gitHead: StrictStr | None = Field(default=None, pattern=_GIT_REVISION.pattern)
    files: list[SourceFileEntryV1] = Field(min_length=1)
    includedUntracked: list[StrictStr] = Field(default_factory=list)
    exclusionPolicyRef: ArtifactRef
    manifestArtifactRef: ArtifactRef

    @model_validator(mode="after")
    def verify_file_set(self):
        paths = [entry.path for entry in self.files]
        if len(paths) != len(set(paths)) or len({path.casefold() for path in paths}) != len(paths):
            raise ValueError("file paths must be unique exactly and case-insensitively")
        untracked = {entry.path for entry in self.files if entry.origin == "untracked"}
        included = set(self.includedUntracked)
        if len(included) != len(self.includedUntracked) or included != untracked:
            raise ValueError("includedUntracked must exactly match untracked file entries")
        return self


class BuildEnvironmentV1(_DTO):
    schemaVersion: Literal[1] = 1
    platform: Literal["windows_x64", "ubuntu_24_04_x64", "wsl_ubuntu_24_04_x64"]
    osVersion: StrictStr = Field(min_length=1, max_length=128)
    kernelVersion: StrictStr = Field(min_length=1, max_length=128)
    architecture: Literal["x86_64"]
    pythonVersion: StrictStr = Field(min_length=1, max_length=32)
    interpreterSha256: StrictStr = Field(pattern=_SHA256.pattern)
    standardLibrarySha256: StrictStr = Field(pattern=_SHA256.pattern)
    toolchainId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    dependencyRefs: list[ArtifactRef] = Field(default_factory=list)


class BuildManifestV1(_DTO):
    schemaVersion: Literal[1] = 1
    sourceStateRef: ArtifactRef
    sourceArtifactRef: ArtifactRef
    environmentRef: ArtifactRef
    buildRecipeRef: ArtifactRef
    protocolRef: ArtifactRef
    fixtureRef: ArtifactRef
    buildOutputRefs: list[ArtifactRef] = Field(min_length=1)
    logsRef: ArtifactRef


class EngineeringPilotProfileV1(_DTO):
    schemaVersion: Literal[1] = 1
    maxCampaignWallTimeMs: Literal[1_200_000] = 1_200_000
    generationDevelopmentMs: Literal[360_000] = 360_000
    generationPublicTestMs: Literal[36_000] = 36_000
    generationModelMs: Literal[324_000] = 324_000
    compileBudgetMs: Literal[60_000] = 60_000
    compileLaunches: Literal[3] = 3
    compileTimeoutMs: Literal[20_000] = 20_000
    evaluationBudgetMs: Literal[480_000] = 480_000
    publicComparisons: Literal[36] = 36
    sealedComparisons: Literal[324] = 324
    securityCases: Literal[16] = 16
    evaluationCaseTimeoutMs: Literal[1_000] = 1_000
    evaluationOracleReserveMs: Literal[60_000] = 60_000
    reportBudgetMs: Literal[120_000] = 120_000
    cleanupBudgetMs: Literal[180_000] = 180_000
    maxSandboxLaunches: Literal[379] = 379
    maxCandidates: Literal[3] = 3
    maxSealedCandidates: Literal[1] = 1
    maxModelTokens: Literal[32_768] = 32_768
    maxOutputTokensPerCompletion: Literal[2_048] = 2_048
    maxSourceBytes: Literal[32_768] = 32_768
    compileMemoryMiB: Literal[512] = 512
    executeMemoryMiB: Literal[256] = 256
    cpuCount: Literal[1] = 1
    scratchMiB: Literal[128] = 128
    maxOutputBytes: Literal[65_536] = 65_536
    maxCloudCostMicrounits: Literal[0] = 0

    @model_validator(mode="after")
    def validate_profile_arithmetic(self):
        generation = self.generationModelMs + self.generationPublicTestMs
        evaluation = (self.sealedComparisons + self.securityCases) * self.evaluationCaseTimeoutMs + self.evaluationOracleReserveMs
        launches = self.compileLaunches + self.publicComparisons + self.sealedComparisons + self.securityCases
        total = self.generationDevelopmentMs + self.compileBudgetMs + self.evaluationBudgetMs + self.reportBudgetMs + self.cleanupBudgetMs
        if (generation != self.generationDevelopmentMs or self.compileLaunches * self.compileTimeoutMs != self.compileBudgetMs
                or evaluation > self.evaluationBudgetMs or launches != self.maxSandboxLaunches
                or total != self.maxCampaignWallTimeMs):
            raise ValueError("pilot phase budgets or sandbox-launch accounting do not reconcile")
        return self


class ResearchProposalV1(_DTO):
    schemaVersion: Literal[1] = 1
    proposalId: StrictStr = Field(min_length=1, max_length=128, pattern=_ID.pattern)
    proposedTargetKind: StrictStr = Field(min_length=1, max_length=128)
    hypothesis: StrictStr = Field(min_length=1, max_length=4096)
    baselineRef: ArtifactRef | None = None
    comparativeProtocolRef: ArtifactRef | None = None
    resourceProfileRef: ArtifactRef | None = None
    riskRefs: list[ArtifactRef] = Field(default_factory=list)
    ownerDecision: Literal[
        "pending", "accept_evidence", "reject", "no_change", "request_followup",
        "approve_promotion_proposal",
    ] = "pending"


class ReviewDecisionV1(_DTO):
    schemaVersion: Literal[1] = 1
    decision: Literal[
        "accept_evidence", "reject", "no_change", "request_followup", "approve_promotion_proposal",
    ]
    rationale: StrictStr = Field(min_length=1, max_length=4096)
