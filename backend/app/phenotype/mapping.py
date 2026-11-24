"""Human-reviewable HPO candidate mapping for extracted phenotype mentions."""
#maps extracted clinical findings to hpo terms

from dataclasses import replace
from typing import Iterable, List, Tuple

from ..hpo.ontology import HpoOntology
from ..hpo.schemas import HPO_RELEASE_METADATA
from .schemas import HpoSuggestion, PhenotypeMention


def map_hpo_candidates(
    mentions: Iterable[PhenotypeMention],
    ontology: HpoOntology,
) -> Tuple[PhenotypeMention, ...]:
    """Return copied mentions with all pinned-ontology candidates attached."""

    if not isinstance(ontology, HpoOntology):
        raise TypeError("ontology must be a loaded HpoOntology")
    if ontology.release_metadata != HPO_RELEASE_METADATA:
        raise ValueError("candidate mapping requires the configured pinned HPO ontology")

    mapped_mentions: List[PhenotypeMention] = []
    for mention in mentions:
        if not isinstance(mention, PhenotypeMention):
            raise TypeError("mentions must contain PhenotypeMention values")
        if mention.hpo_version != ontology.release:
            raise ValueError("mention HPO version does not match the loaded ontology")

        lookup = ontology.search(mention.phenotype_text)
        discovered_suggestions = tuple(
            HpoSuggestion(
                canonical_hpo_id=item.canonical_hpo_id,
                matched_hpo_id=item.matched_hpo_id,
                term_name=item.term_name,
                match_type=item.match_type,
                explanation=item.explanation,
                is_obsolete=item.is_obsolete,
                replacement_ids=item.replacement_ids,
                consider_ids=item.consider_ids,
                human_review_required=item.human_review_required,
            )
            for item in lookup.suggestions
        )
        combined_suggestions: List[HpoSuggestion] = []
        for suggestion in mention.suggestions + discovered_suggestions:
            if suggestion not in combined_suggestions:
                combined_suggestions.append(suggestion)
        suggestions = tuple(combined_suggestions)
        mapped_mentions.append(
            replace(
                mention,
                suggestions=suggestions,
                hpo_mapping_ambiguous=(
                    mention.hpo_mapping_ambiguous
                    or lookup.ambiguous
                    or len(suggestions) > 1
                ),
            )
        )

    return tuple(mapped_mentions)
