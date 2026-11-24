"""Immutable Exomiser run, gene and variant result contracts."""

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class ExomiserRawEvidence:
    evidence_id: str
    evidence_type: str
    value: str


@dataclass(frozen=True)
class ExomiserVariantEvidence:
    chrom: Optional[str]
    pos: Optional[int]
    ref: Optional[str]
    alt: Optional[str]
    vcf_id: Optional[str]
    filter: Optional[str]
    mode_of_inheritance: Optional[str]
    variant_score: Optional[float]
    raw_record: Mapping[str, str]
    raw_evidence: Tuple[ExomiserRawEvidence, ...]


@dataclass(frozen=True)
class ExomiserGeneResult:
    rank: Optional[int]
    gene_symbol: Optional[str]
    entrez_gene_id: Optional[str]
    mode_of_inheritance: Optional[str]
    combined_score: Optional[float]
    phenotype_score: Optional[float]
    variant_score: Optional[float]
    human_phenotype_score: Optional[float]
    mouse_phenotype_score: Optional[float]
    fish_phenotype_score: Optional[float]
    raw_record: Mapping[str, str]
    unknown_fields: Mapping[str, str]
    evidence: Tuple[ExomiserRawEvidence, ...]
    variants: Tuple[ExomiserVariantEvidence, ...] = ()


@dataclass(frozen=True)
class ExomiserRunManifest:
    run_id: str
    exomiser_version: str
    exomiser_distribution_sha256: str
    reference_data_identity: str
    reference_data_sha256: str
    hpo_release: str
    phenotype_snapshot_id: str
    vcf_sha256: str
    vcf_sample_id: str
    assembly_label: str
    java_version: str
    configuration_sha256: str


@dataclass(frozen=True)
class ExomiserRunResult:
    manifest: ExomiserRunManifest
    genes: Tuple[ExomiserGeneResult, ...]
    warnings: Tuple[str, ...]
    parse_issues: Tuple[str, ...]
    raw_output_sha256: Optional[str]
