"""Conservative parsing for verified Exomiser TSV outputs."""

import csv
import io
from types import MappingProxyType

from .exceptions import ExomiserExecutionError
from .schemas import (
    ExomiserGeneResult,
    ExomiserRawEvidence,
    ExomiserVariantEvidence,
)


GENE_FIELDS = {
    "RANK",
    "GENE_SYMBOL",
    "ENTREZ_GENE_ID",
    "MOI",
    "EXOMISER_GENE_COMBINED_SCORE",
    "EXOMISER_GENE_PHENO_SCORE",
    "EXOMISER_GENE_VARIANT_SCORE",
    "HUMAN_PHENO_SCORE",
    "MOUSE_PHENO_SCORE",
    "FISH_PHENO_SCORE",
    "HUMAN_PHENO_EVIDENCE",
    "MOUSE_PHENO_EVIDENCE",
    "FISH_PHENO_EVIDENCE",
}

VARIANT_FIELDS = {
    "RANK",
    "GENE_SYMBOL",
    "ENTREZ_GENE_ID",
    "MOI",
    "EXOMISER_GENE_COMBINED_SCORE",
    "EXOMISER_GENE_PHENO_SCORE",
    "EXOMISER_GENE_VARIANT_SCORE",
    "EXOMISER_VARIANT_SCORE",
    "CONTRIBUTING_VARIANT",
    "WHITELIST_VARIANT",
    "VCF_ID",
    "REF",
    "ALT",
    "FILTER",
}


def _optional_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_exomiser_tsv_gene(text):
    if not isinstance(text, str):
        raise TypeError("TSV gene text must be a string")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None:
        raise ExomiserExecutionError("TSV_GENE output has no header")
    required = {"RANK", "GENE_SYMBOL"}
    missing = required - set(reader.fieldnames)
    if missing:
        raise ExomiserExecutionError(
            "TSV_GENE missing required headers: {}".format(
                ", ".join(sorted(missing))
            )
        )

    genes = []
    for row_index, row in enumerate(reader, start=2):
        raw = MappingProxyType(dict(row))
        unknown = MappingProxyType(
            {
                key: value
                for key, value in row.items()
                if key not in GENE_FIELDS and value is not None
            }
        )
        evidence = []
        for field in (
            "HUMAN_PHENO_EVIDENCE",
            "MOUSE_PHENO_EVIDENCE",
            "FISH_PHENO_EVIDENCE",
        ):
            value = row.get(field)
            if value:
                evidence.append(
                    ExomiserRawEvidence(
                        evidence_id="TSV_GENE:{}:{}".format(row_index, field),
                        evidence_type=field,
                        value=value,
                    )
                )
        genes.append(
            ExomiserGeneResult(
                rank=_optional_int(row.get("RANK")),
                gene_symbol=row.get("GENE_SYMBOL") or None,
                entrez_gene_id=row.get("ENTREZ_GENE_ID") or None,
                mode_of_inheritance=row.get("MOI") or None,
                combined_score=_optional_float(
                    row.get("EXOMISER_GENE_COMBINED_SCORE")
                ),
                phenotype_score=_optional_float(
                    row.get("EXOMISER_GENE_PHENO_SCORE")
                ),
                variant_score=_optional_float(
                    row.get("EXOMISER_GENE_VARIANT_SCORE")
                ),
                human_phenotype_score=_optional_float(
                    row.get("HUMAN_PHENO_SCORE")
                ),
                mouse_phenotype_score=_optional_float(
                    row.get("MOUSE_PHENO_SCORE")
                ),
                fish_phenotype_score=_optional_float(
                    row.get("FISH_PHENO_SCORE")
                ),
                raw_record=raw,
                unknown_fields=unknown,
                evidence=tuple(evidence),
            )
        )
    return tuple(genes)


def parse_exomiser_tsv_variant(text):
    if not isinstance(text, str):
        raise TypeError("TSV variant text must be a string")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None:
        raise ExomiserExecutionError("TSV_VARIANT output has no header")
    required = {"RANK", "GENE_SYMBOL", "VCF_ID", "REF", "ALT"}
    missing = required - set(reader.fieldnames)
    if missing:
        raise ExomiserExecutionError(
            "TSV_VARIANT missing required headers: {}".format(
                ", ".join(sorted(missing))
            )
        )

    variants = []
    for row_index, row in enumerate(reader, start=2):
        raw = MappingProxyType(dict(row))
        evidence = tuple(
            ExomiserRawEvidence(
                evidence_id="TSV_VARIANT:{}:{}".format(row_index, field),
                evidence_type=field,
                value=row[field],
            )
            for field in (
                "CONTRIBUTING_VARIANT",
                "WHITELIST_VARIANT",
                "FILTER",
                "EXOMISER_ACMG_CLASSIFICATION",
            )
            if field in row and row[field]
        )
        variants.append(
            ExomiserVariantEvidence(
                chrom=None,
                pos=None,
                ref=row.get("REF") or None,
                alt=row.get("ALT") or None,
                vcf_id=row.get("VCF_ID") or None,
                filter=row.get("FILTER") or None,
                mode_of_inheritance=row.get("MOI") or None,
                variant_score=_optional_float(
                    row.get("EXOMISER_VARIANT_SCORE")
                ),
                raw_record=raw,
                raw_evidence=evidence,
            )
        )
    return tuple(variants)
