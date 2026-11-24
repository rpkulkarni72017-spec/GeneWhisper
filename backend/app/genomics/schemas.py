"""Immutable schemas for structurally validated VCF evidence."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .assembly import AssemblyDeclaration, AssemblyStatus


class GenotypeSeparator(str, Enum):
    UNPHASED = "/"
    PHASED = "|"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class VariantIssueCategory(str, Enum):
    MALFORMED_RECORD = "malformed_record"
    UNSUPPORTED_ALLELE = "unsupported_allele"
    UNSUPPORTED_FIELD = "unsupported_field"
    DUPLICATE_RECORD = "duplicate_record"
    UNVERIFIABLE_PL = "unverifiable_pl"
    ASSEMBLY_AMBIGUITY = "assembly_ambiguity"


@dataclass(frozen=True)
class VcfSourceIdentity:
    source_ref: str
    sha256: str
    size_bytes: int
    vcf_format: str


@dataclass(frozen=True)
class HeaderFieldDeclaration:
    field_id: str
    number: str
    field_type: str
    description: str


@dataclass(frozen=True)
class ContigMetadata:
    contig_id: str
    length: Optional[int]
    assembly_labels: Tuple[str, ...]


@dataclass(frozen=True)
class InfoFieldEvidence:
    key: str
    raw_value: Optional[str]


@dataclass(frozen=True)
class FormatFieldEvidence:
    key: str
    raw_value: str
    typed_value: object = None


@dataclass(frozen=True)
class GenotypeEvidence:
    allele_indices: Tuple[Optional[int], ...]
    separator: Optional[GenotypeSeparator]
    is_missing: bool
    raw_gt: str


@dataclass(frozen=True)
class SampleGenotypeEvidence:
    sample_id: str
    sample_column: int
    gt: Optional[GenotypeEvidence]
    format_fields: Tuple[FormatFieldEvidence, ...]
    dp: Optional[int] = None
    gq: Optional[int] = None
    ad: Optional[Tuple[Optional[int], ...]] = None
    pl: Optional[Tuple[Optional[int], ...]] = None


@dataclass(frozen=True)
class ValidatedVariantRecord:
    source_record_id: str
    line_number: int
    chrom: str
    pos: int
    id_values: Tuple[str, ...]
    ref: str
    alt: Tuple[str, ...]
    qual: Optional[float]
    raw_qual: Optional[str]
    filters: Tuple[str, ...]
    raw_info: Tuple[InfoFieldEvidence, ...]
    raw_format: Tuple[str, ...]
    samples: Tuple[SampleGenotypeEvidence, ...]
    raw_fixed_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateEvidence:
    duplicate_key: Tuple[str, int, str, Tuple[str, ...]]
    line_numbers: Tuple[int, ...]
    source_record_ids: Tuple[str, ...]


@dataclass(frozen=True)
class RecordIssueEvidence:
    line_number: int
    fixed_fields: Tuple[str, ...]
    raw_format: Tuple[str, ...]
    raw_samples: Tuple[str, ...]
    sample_ids: Tuple[str, ...]
    raw_info: Tuple[InfoFieldEvidence, ...]


@dataclass(frozen=True)
class VariantIssue:
    issue_id: str
    category: VariantIssueCategory
    severity: IssueSeverity
    line_number: int
    reason: str
    raw_chrom: str
    raw_pos: str
    raw_ref: str
    raw_alt: str
    record: Optional[RecordIssueEvidence] = None
    duplicate: Optional[DuplicateEvidence] = None


@dataclass(frozen=True)
class VcfMetadata:
    source: VcfSourceIdentity
    vcf_version: str
    assembly_status: AssemblyStatus
    assembly_declarations: Tuple[AssemblyDeclaration, ...]
    contigs: Tuple[ContigMetadata, ...]
    sample_ids: Tuple[str, ...]
    info_fields: Tuple[HeaderFieldDeclaration, ...]
    format_fields: Tuple[HeaderFieldDeclaration, ...]


@dataclass(frozen=True)
class VcfIngestionResult:
    metadata: VcfMetadata
    accepted_records: Tuple[ValidatedVariantRecord, ...]
    issues: Tuple[VariantIssue, ...]
