"""Explicit VCF sample and Phenopacket binding."""

from dataclasses import dataclass
from typing import Tuple

from ..genomics.assembly import AssemblyStatus
from .assembly import AssemblyCompatibility, UnresolvedAssemblyMapper
from .exceptions import ExomiserBindingError


@dataclass(frozen=True)
class ExomiserCaseBinding:
    case_id: str
    phenotype_snapshot_id: str
    phenopacket_id: str
    subject_id: str
    vcf_source_ref: str
    vcf_sha256: str
    vcf_sample_id: str
    phenopacket_assembly: str
    vcf_assembly_status: AssemblyStatus
    vcf_assembly_labels: Tuple[str, ...]


def validate_exomiser_binding(
    binding,
    *,
    vcf_metadata,
    phenotype_snapshot=None,
    assembly_mapper=None,
):
    """Validate identity and assembly without selecting samples or assemblies."""

    if not isinstance(binding, ExomiserCaseBinding):
        raise TypeError("binding must be an ExomiserCaseBinding")
    if not binding.case_id or not binding.vcf_sample_id:
        raise ExomiserBindingError("case ID and VCF sample ID are required")
    if binding.vcf_sample_id not in vcf_metadata.sample_ids:
        raise ExomiserBindingError(
            "selected VCF sample is not present: {}".format(binding.vcf_sample_id)
        )
    if len(vcf_metadata.sample_ids) > 1 and not binding.vcf_sample_id:
        raise ExomiserBindingError(
            "explicit VCF sample selection is required for multi-sample input"
        )
    if binding.vcf_sha256 != vcf_metadata.source.sha256:
        raise ExomiserBindingError("binding VCF SHA-256 does not match metadata")
    if phenotype_snapshot is not None and (
        binding.subject_id != phenotype_snapshot.subject_id
    ):
        raise ExomiserBindingError(
            "binding subject does not match Phenopacket subject"
        )
    if binding.vcf_assembly_status in (
        AssemblyStatus.EXPLICIT_CONFLICTING,
        AssemblyStatus.UNKNOWN,
        AssemblyStatus.AMBIGUOUS,
    ):
        raise ExomiserBindingError(
            "assembly compatibility is unresolved for this binding"
        )
    if binding.vcf_assembly_status != AssemblyStatus.EXPLICIT_SINGLE:
        raise ExomiserBindingError("explicit VCF assembly status is required")
    if not binding.vcf_assembly_labels:
        raise ExomiserBindingError("at least one VCF assembly label is required")

    mapper = assembly_mapper or UnresolvedAssemblyMapper()
    compatibility = mapper.compatible(
        phenopacket_label=binding.phenopacket_assembly,
        exomiser_label=binding.vcf_assembly_labels[0],
    )
    if compatibility != AssemblyCompatibility.COMPATIBLE:
        raise ExomiserBindingError(
            "assembly compatibility is not explicitly compatible"
        )
    return binding
