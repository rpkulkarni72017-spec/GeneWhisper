"""Exomiser execution preflight."""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from ..genomics.assembly import AssemblyStatus
from .exceptions import ExomiserPreflightError


@dataclass(frozen=True)
class ExomiserPreflightIssue:
    code: str
    message: str
    evidence_id: Optional[str] = None
    severity: str = "error"


@dataclass(frozen=True)
class ExomiserPreflightReport:
    case_id: str
    snapshot_id: str
    can_execute: bool
    errors: Tuple[ExomiserPreflightIssue, ...]
    warnings: Tuple[ExomiserPreflightIssue, ...]


def preflight_exomiser(
    *,
    binding,
    vcf_ingestion,
    phenotype_snapshot,
    config,
    assembly_compatibility,
):
    """Produce typed hard-failure and warning lists without invoking Exomiser."""

    errors = []
    warnings = []

    if binding.phenotype_snapshot_id != phenotype_snapshot.phenotype_snapshot_id:
        errors.append(
            ExomiserPreflightIssue(
                "STALE_PHENOTYPE_SNAPSHOT",
                "binding and phenotype snapshot IDs differ",
                binding.phenotype_snapshot_id,
            )
        )
    if binding.vcf_sha256 != vcf_ingestion.metadata.source.sha256:
        errors.append(
            ExomiserPreflightIssue(
                "STALE_VCF_SOURCE",
                "binding VCF SHA-256 differs from ingestion metadata",
                binding.vcf_sha256,
            )
        )
    if binding.subject_id != phenotype_snapshot.subject_id:
        errors.append(
            ExomiserPreflightIssue(
                "SUBJECT_MISMATCH",
                "binding subject differs from Phenopacket subject",
                binding.subject_id,
            )
        )
    if binding.vcf_sample_id not in vcf_ingestion.metadata.sample_ids:
        errors.append(
            ExomiserPreflightIssue(
                "UNKNOWN_VCF_SAMPLE",
                "explicit VCF sample is absent",
                binding.vcf_sample_id,
            )
        )
    if len(vcf_ingestion.metadata.sample_ids) > 1 and not binding.vcf_sample_id:
        errors.append(
            ExomiserPreflightIssue(
                "AMBIGUOUS_VCF_SAMPLE",
                "multi-sample VCF requires explicit selection",
            )
        )
    if vcf_ingestion.metadata.assembly_status in (
        AssemblyStatus.EXPLICIT_CONFLICTING,
        AssemblyStatus.UNKNOWN,
        AssemblyStatus.AMBIGUOUS,
    ):
        errors.append(
            ExomiserPreflightIssue(
                "ASSEMBLY_UNRESOLVED",
                "assembly is conflicting, unknown or ambiguous",
            )
        )
    if assembly_compatibility.value != "compatible":
        errors.append(
            ExomiserPreflightIssue(
                "ASSEMBLY_NOT_COMPATIBLE",
                "assembly compatibility is not explicitly compatible",
            )
        )
    if vcf_ingestion.issues:
        malformed = [
            issue
            for issue in vcf_ingestion.issues
            if issue.category.value in (
                "malformed_record",
                "unsupported_allele",
                "unsupported_field",
            )
        ]
        if malformed:
            for issue in malformed:
                warnings.append(
                    ExomiserPreflightIssue(
                        "VCF_RECORD_ISSUE",
                        issue.reason,
                        issue.issue_id,
                        "warning",
                    )
                )
            if any(
                issue.category.value in (
                    "malformed_record",
                    "unsupported_field",
                    "unsupported_allele",
                )
                and issue.severity.value == "error"
                for issue in malformed
            ):
                errors.append(
                    ExomiserPreflightIssue(
                        "MALFORMED_REQUIRED_VCF_RECORD",
                        "malformed required VCF evidence blocks execution",
                    )
                )
    if config is None:
        errors.append(
            ExomiserPreflightIssue(
                "MISSING_EXOMISER_CONFIG",
                "Exomiser invocation configuration is missing",
            )
        )
    if not phenotype_snapshot.positive_hpo_ids:
        warnings.append(
            ExomiserPreflightIssue(
                "NO_CONFIRMED_POSITIVE_HPO",
                "no confirmed patient-positive HPO findings",
                severity="warning",
            )
        )
    for hpo_id in phenotype_snapshot.positive_hpo_ids:
        if not re.fullmatch(r"HP:\d+", hpo_id):
            errors.append(
                ExomiserPreflightIssue(
                    "INVALID_HPO_ID",
                    "positive phenotype snapshot contains invalid HPO ID",
                    hpo_id,
                )
            )

    report = ExomiserPreflightReport(
        case_id=binding.case_id,
        snapshot_id=phenotype_snapshot.phenotype_snapshot_id,
        can_execute=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
    if not report.can_execute:
        raise ExomiserPreflightError(
            "Exomiser preflight failed: {}".format(
                "; ".join(issue.code for issue in report.errors)
            )
        )
    return report
