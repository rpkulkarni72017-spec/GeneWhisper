"""Extraction adapter contracts and deterministic mention construction."""
#extracting symptomsfrom clinical text

from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

from ..hpo.schemas import HPO_RELEASE_METADATA
from .schemas import (
    AssertionStatus,
    Experiencer,
    PhenotypeMention,
    SourceDocumentRevision,
    Temporality,
    validate_mention_evidence,
)


@dataclass(frozen=True)
class ExtractionResult:
    """One externally supplied extraction result; no NLP is performed here."""

    mention_id: str
    start_offset: int
    end_offset: int
    phenotype_text: str
    assertion: AssertionStatus
    temporality: Temporality
    experiencer: Experiencer
    family_relationship: Optional[str] = None
    extraction_confidence: Optional[float] = None


class PhenotypeExtractor(Protocol):
    """Boundary for future extractors that return mentions for one document."""

    def extract(
        self,
        document: SourceDocumentRevision,
        *,
        extractor_version: str,
    ) -> Tuple[PhenotypeMention, ...]:
        """Return mentions linked to the exact supplied document revision."""
        ...


def build_mentions_from_results(
    document: SourceDocumentRevision,
    results: Tuple[ExtractionResult, ...],
    *,
    extractor_version: str,
) -> Tuple[PhenotypeMention, ...]:
    """Adapt supplied extraction results into validated immutable mentions."""

    if not isinstance(document, SourceDocumentRevision):
        raise TypeError("document must be a SourceDocumentRevision")
    if not isinstance(extractor_version, str) or not extractor_version.strip():
        raise ValueError("extractor_version must be a non-empty string")
    if not isinstance(results, tuple):
        raise TypeError("results must be a tuple of ExtractionResult values")

    mentions: List[PhenotypeMention] = []
    for result in results:
        if not isinstance(result, ExtractionResult):
            raise TypeError("results must contain ExtractionResult values")
        if (
            isinstance(result.start_offset, bool)
            or isinstance(result.end_offset, bool)
            or not isinstance(result.start_offset, int)
            or not isinstance(result.end_offset, int)
        ):
            raise TypeError("extraction offsets must be integer code-point offsets")
        if (
            result.start_offset < 0
            or result.end_offset <= result.start_offset
            or result.end_offset > len(document.text)
        ):
            raise ValueError("extraction offsets must define a span within the document")

        mention = PhenotypeMention(
            mention_id=result.mention_id,
            case_id=document.case_id,
            source_document_ref=document.document_ref,
            original_text=document.text[result.start_offset : result.end_offset],
            start_offset=result.start_offset,
            end_offset=result.end_offset,
            phenotype_text=result.phenotype_text,
            assertion=result.assertion,
            temporality=result.temporality,
            experiencer=result.experiencer,
            hpo_version=HPO_RELEASE_METADATA.release,
            family_relationship=result.family_relationship,
            extraction_confidence=result.extraction_confidence,
            extractor_version=extractor_version,
            source_document_revision=document.revision,
        )
        validate_mention_evidence(document, mention)
        mentions.append(mention)

    return tuple(mentions)
