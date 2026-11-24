"""Human-reviewed phenotype projection for Exomiser v1 Phenopackets."""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from ..phenotype.review import latest_review_events
from ..phenotype.schemas import (
    AssertionStatus,
    Experiencer,
    ReviewDecision,
)


@dataclass(frozen=True)
class ExomiserPhenotypeProvenance:
    mention_id: str
    case_id: str
    hpo_id: Optional[str]
    review_event_id: Optional[str]
    review_status: str
    exclusion_reason: Optional[str]
    temporality: str
    source_document_ref: str


@dataclass(frozen=True)
class ExomiserPhenotypeSnapshot:
    case_id: str
    phenotype_snapshot_id: str
    hpo_release: str
    subject_id: str
    phenopacket_assembly: str
    positive_hpo_ids: Tuple[str, ...]
    phenopacket_yaml: str
    provenance: Tuple[ExomiserPhenotypeProvenance, ...]


def project_exomiser_phenopacket(
    *,
    case_id,
    phenotype_snapshot_id,
    subject_id,
    hts_uri,
    phenopacket_assembly,
    hpo_release,
    mentions,
    review_events,
    confirmed_positive_set,
):
    """Project only confirmed patient-positive findings into Exomiser input."""

    positive_ids = tuple(
        phenotype.hpo_id
        for phenotype in confirmed_positive_set.phenotypes
        if phenotype.case_id == case_id
    )
    positive_ids = tuple(dict.fromkeys(positive_ids))

    latest = latest_review_events(review_events)
    provenance = []
    for mention in mentions:
        if mention.case_id != case_id:
            continue
        event = latest.get(mention.mention_id)
        hpo_id = event.selected_hpo_id if event is not None else None
        review_status = event.decision.value if event is not None else "pending"
        exclusion_reason = None

        if hpo_id not in positive_ids:
            if event is None:
                exclusion_reason = "pending_review"
            elif event.decision == ReviewDecision.REJECTED:
                exclusion_reason = "rejected"
            elif event.decision == ReviewDecision.CONFIRMED_NEGATED:
                exclusion_reason = "confirmed_negated"
            elif event.decision == ReviewDecision.DEFERRED:
                exclusion_reason = "deferred"
            elif mention.assertion == AssertionStatus.NEGATED:
                exclusion_reason = "negated_assertion"
            elif mention.assertion == AssertionStatus.UNCERTAIN:
                exclusion_reason = "uncertain_assertion"
            elif mention.experiencer != Experiencer.PATIENT:
                exclusion_reason = "non_patient_experiencer"
            elif hpo_id is None:
                exclusion_reason = "no_human_selected_hpo"
            else:
                exclusion_reason = "not_confirmed_patient_positive"

        provenance.append(
            ExomiserPhenotypeProvenance(
                mention_id=mention.mention_id,
                case_id=mention.case_id,
                hpo_id=hpo_id,
                review_event_id=event.event_id if event is not None else None,
                review_status=review_status,
                exclusion_reason=exclusion_reason,
                temporality=mention.temporality.value,
                source_document_ref=mention.source_document_ref,
            )
        )

    phenotypic_features = []
    for hpo_id in positive_ids:
        phenotypic_features.append(
            {
                "type": {
                    "id": hpo_id,
                    "label": "",
                },
                "negated": False,
            }
        )

    document = {
        "id": subject_id,
        "subject": {"id": subject_id},
        "phenotypicFeatures": phenotypic_features,
        "htsFiles": [
            {
                "uri": hts_uri,
                "htsFormat": "VCF",
                "genomeAssembly": phenopacket_assembly,
            }
        ],
        "metaData": {
            "phenopacketSchemaVersion": "1.0",
            "resources": [
                {
                    "id": "hpo",
                    "name": "human phenotype ontology",
                    "namespacePrefix": "HP",
                    "version": hpo_release,
                }
            ],
        },
    }
    return ExomiserPhenotypeSnapshot(
        case_id=case_id,
        phenotype_snapshot_id=phenotype_snapshot_id,
        hpo_release=hpo_release,
        subject_id=subject_id,
        phenopacket_assembly=phenopacket_assembly,
        positive_hpo_ids=positive_ids,
        phenopacket_yaml=json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        provenance=tuple(provenance),
    )
