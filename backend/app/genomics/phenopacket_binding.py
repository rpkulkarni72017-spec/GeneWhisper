"""Future binding contract between a Phenopacket subject and a VCF."""

from dataclasses import dataclass

from .assembly import AssemblyCompatibility


@dataclass(frozen=True)
class PhenopacketVariantBinding:
    case_id: str
    phenopacket_id: str
    subject_id: str
    hts_uri: str
    hts_sha256: str
    vcf_sample_id: str
    phenopacket_assembly: str
    vcf_assembly_status: str
    compatibility: AssemblyCompatibility
