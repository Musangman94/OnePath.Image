"""Contract validation only; no candidate is imported, compiled, or executed."""

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from backend.app.schemas.cae import (
    ArtifactRef, BuildEnvironmentV1, BuildManifestV1, CandidateManifestV2,
    EngineeringPilotProfileV1, EvolutionTargetV2, ResearchAuthorizationV1,
    ResearchBudgetV1, ResearchProposalV1, ReviewDecisionV1, SourceStateManifestV1,
)


def ref(name="artifact", version=None):
    result = {"id": name, "sha256": "a" * 64}
    if version is not None:
        result["version"] = version
    return result


def target(**overrides):
    value = {
        "evolutionType": "engineering",
        "targetKind": "algorithm.module",
        "payloadSchemaRef": ref("schema"),
        "intendedPromotionAuthority": "nontrusted_code",
        "dimensions": ["reliability"],
    }
    return {**value, **overrides}


def candidate(**overrides):
    value = {
        "schemaVersion": 2,
        "candidateId": "candidate-1",
        "scopeRef": ref("scope"),
        "target": target(),
        "baselineRef": ref("baseline"),
        "parentVersionRef": None,
        "payloadRef": ref("payload"),
        "sourceStateRef": ref("source-state"),
        "environmentRef": ref("environment"),
        "buildRecipeRef": ref("recipe"),
        "evaluationProtocolRef": ref("evaluation"),
        "resourceProfileRef": ref("resources"),
        "provenanceRefs": [ref("provenance")],
        "dependencyRefs": [],
        "requestedCapabilities": ["compile_isolated_candidate"],
        "rollbackRef": ref("rollback"),
    }
    return {**value, **overrides}


def budget(**overrides):
    value = {
        "maxWallTimeMs": 1_000,
        "maxModelTokens": 2_000,
        "maxOutputTokens": 500,
        "maxSourceBytes": 10_000,
        "maxSandboxLaunches": 3,
        "maxScratchBytes": 100_000,
        "maxOutputBytes": 10_000,
        "maxCloudCostMicrounits": 0,
    }
    return {**value, **overrides}


def authorization(**overrides):
    value = {
        "authorizationId": "auth-1",
        "ownerPrincipalId": "owner-1",
        "scopeRef": ref("scope"),
        "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "expiresAt": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "allowedEvolutionTypes": ["operational", "engineering"],
        "allowedTargetKinds": ["algorithm.module"],
        "allowedCapabilities": ["compile_isolated_candidate"],
        "allowedModelRefs": [],
        "allowedBuildRecipeRefs": [],
        "allowedEnvironmentRefs": [],
        "allowedSourceStateRefs": [],
        "boundedBudget": budget(),
        "cloudConsentRef": None,
        "productionPromotionAuthorized": False,
    }
    return {**value, **overrides}


def file_entry(path="src/agent.py", origin="tracked"):
    return {"path": path, "sizeBytes": 12, "sha256": "b" * 64, "origin": origin}


class CaeContractTests(unittest.TestCase):
    def test_artifact_ref_requires_lowercase_sha_and_positive_version(self):
        self.assertEqual(ArtifactRef.model_validate(ref(version=1)).version, 1)
        for value in ({**ref(), "sha256": "A" * 64}, {**ref(), "version": 0}, {**ref(), "version": True}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ArtifactRef.model_validate(value)

    def test_target_allows_registered_kind_and_requires_unique_dimensions(self):
        self.assertEqual(EvolutionTargetV2.model_validate(target()).targetKind, "algorithm.module")
        with self.assertRaises(ValidationError):
            EvolutionTargetV2.model_validate(target(dimensions=["reliability", "reliability"]))

    def test_candidate_manifest_is_explicit_and_versioned(self):
        item = CandidateManifestV2.model_validate(candidate())
        self.assertEqual(item.schemaVersion, 2)
        with self.assertRaises(ValidationError):
            CandidateManifestV2.model_validate({k: v for k, v in candidate().items() if k != "rollbackRef"})
        with self.assertRaises(ValidationError):
            CandidateManifestV2.model_validate({**candidate(), "unreviewed": True})

    def test_executable_target_is_rejected_but_proposal_can_preserve_it(self):
        with self.assertRaises(ValidationError):
            CandidateManifestV2.model_validate(candidate(target=target(targetKind="unknown.future.kind")))
        proposal = ResearchProposalV1.model_validate({
            "proposalId": "proposal-1", "proposedTargetKind": "unknown.future.kind",
            "hypothesis": "Compare a future execution model.", "ownerDecision": "pending",
        })
        self.assertEqual(proposal.ownerDecision, "pending")
        self.assertFalse(hasattr(proposal, "execute"))

    def test_authorization_accepts_only_operational_or_engineering_scope(self):
        self.assertEqual(len(ResearchAuthorizationV1.model_validate(authorization()).allowedEvolutionTypes), 2)
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(allowedEvolutionTypes=["intelligence_research"]))

    def test_authorization_is_not_promotion_authority_or_authentication(self):
        for claimed in (True, 0, 1, "false"):
            with self.subTest(claimed=claimed), self.assertRaises(ValidationError):
                ResearchAuthorizationV1.model_validate(authorization(productionPromotionAuthorized=claimed))

    def test_authorization_requires_utc_ordered_expiry_and_exactly_one_budget(self):
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(expiresAt=datetime(2025, 1, 1, tzinfo=timezone.utc)))
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(issuedAt=datetime(2026, 1, 1)))
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(budgetsRef=ref("budget")))
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(boundedBudget=None))

    def test_positive_cloud_budget_requires_explicit_consent(self):
        with self.assertRaises(ValidationError):
            ResearchAuthorizationV1.model_validate(authorization(
                boundedBudget=budget(maxCloudCostMicrounits=1), cloudConsentRef=None))
        accepted = ResearchAuthorizationV1.model_validate(authorization(
            boundedBudget=budget(maxCloudCostMicrounits=1), cloudConsentRef=ref("cloud-consent")))
        self.assertEqual(accepted.boundedBudget.maxCloudCostMicrounits, 1)

    def test_source_paths_reject_traversal_absolute_drive_unc_and_bad_components(self):
        for path in ("../x.py", "./x.py", "/x.py", "C:/x.py", "//server/x.py", "a\\b.py",
                     "a//b.py", "a/../b.py", "x.py.", "x.py ", "CON.txt", "aux", "a\x00b.py"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                SourceStateManifestV1.model_validate({
                    "sourceStateId": "state-1", "repositoryId": "repo-1", "scopeRef": ref("scope"),
                    "files": [file_entry(path)], "includedUntracked": [],
                    "exclusionPolicyRef": ref("exclusions"), "manifestArtifactRef": ref("manifest"),
                })

    def test_source_state_requires_exact_unique_untracked_set_and_casefold_unique_paths(self):
        base = {
            "sourceStateId": "state-1", "repositoryId": "repo-1", "scopeRef": ref("scope"),
            "files": [file_entry("src/a.py"), file_entry("notes.txt", "untracked")],
            "includedUntracked": ["notes.txt"], "exclusionPolicyRef": ref("exclusions"),
            "manifestArtifactRef": ref("manifest"),
        }
        self.assertEqual(len(SourceStateManifestV1.model_validate(base).files), 2)
        for change in ({"includedUntracked": []}, {"includedUntracked": ["notes.txt", "notes.txt"]},
                       {"files": [file_entry("src/a.py"), file_entry("SRC/A.PY", "generated")], "includedUntracked": []}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                SourceStateManifestV1.model_validate({**base, **change})

    def test_environment_and_build_require_hashed_toolchain_and_source_refs(self):
        environment = BuildEnvironmentV1.model_validate({
            "platform": "ubuntu_24_04_x64", "osVersion": "24.04", "kernelVersion": "6.x",
            "architecture": "x86_64", "pythonVersion": "3.13.15", "interpreterSha256": "c" * 64,
            "standardLibrarySha256": "d" * 64, "toolchainId": "toolchain-1", "dependencyRefs": [],
        })
        self.assertEqual(environment.platform, "ubuntu_24_04_x64")
        build = {
            "sourceStateRef": ref("source"), "sourceArtifactRef": ref("source-archive"),
            "environmentRef": ref("environment"), "buildRecipeRef": ref("recipe"),
            "protocolRef": ref("protocol"), "fixtureRef": ref("fixture"),
            "buildOutputRefs": [ref("output")], "logsRef": ref("logs"),
        }
        self.assertEqual(len(BuildManifestV1.model_validate(build).buildOutputRefs), 1)
        with self.assertRaises(ValidationError):
            BuildManifestV1.model_validate({k: v for k, v in build.items() if k != "environmentRef"})

    def test_pilot_profile_constants_and_arithmetic_reconcile(self):
        profile = EngineeringPilotProfileV1()
        self.assertEqual(profile.maxSandboxLaunches, 379)
        self.assertEqual(profile.sealedComparisons * 1000 + profile.securityCases * 1000
                         + profile.evaluationOracleReserveMs, 400_000)
        with self.assertRaises(ValidationError):
            EngineeringPilotProfileV1(maxCampaignWallTimeMs=1_200_001)
        with self.assertRaises(ValidationError):
            EngineeringPilotProfileV1(maxSandboxLaunches=True)

    def test_review_approval_is_only_a_proposal_decision(self):
        decision = ReviewDecisionV1.model_validate({
            "decision": "approve_promotion_proposal", "rationale": "Evidence is sufficient for owner review.",
        })
        self.assertEqual(decision.decision, "approve_promotion_proposal")
        with self.assertRaises(ValidationError):
            ReviewDecisionV1.model_validate({"decision": "deploy", "rationale": "go"})


if __name__ == "__main__":
    unittest.main()
