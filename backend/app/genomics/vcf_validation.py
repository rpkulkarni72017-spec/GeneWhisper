"""Streaming read-only ingestion for uncompressed VCF 4.1-4.3."""

import hashlib
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from .assembly import AssemblyDeclaration, AssemblyStatus
from .exceptions import (
    AssemblyDeclarationError,
    MalformedVcfHeaderError,
    UnsupportedVcfFormatError,
)
from .schemas import (
    ContigMetadata,
    DuplicateEvidence,
    FormatFieldEvidence,
    GenotypeEvidence,
    GenotypeSeparator,
    HeaderFieldDeclaration,
    InfoFieldEvidence,
    IssueSeverity,
    RecordIssueEvidence,
    SampleGenotypeEvidence,
    ValidatedVariantRecord,
    VariantIssue,
    VariantIssueCategory,
    VcfIngestionResult,
    VcfMetadata,
    VcfSourceIdentity,
)


SUPPORTED_VCF_VERSIONS = ("4.1", "4.2", "4.3")
FIXED_COLUMNS = ("CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO")
NUCLEOTIDE_PATTERN = re.compile(r"^[ACGTNacgtn]+$")
VCF_FILEFORMAT_PATTERN = re.compile(r"^VCFv(\d+\.\d+)$")


def pl_cardinality(allele_count: int, ploidy: int) -> int:
    """Return C(a+p-1,p), the Number=G PL value count."""

    if type(allele_count) is not int or allele_count < 1:
        raise ValueError("allele_count must be a positive integer")
    if type(ploidy) is not int or ploidy < 1:
        raise ValueError("ploidy must be a positive integer")
    return math.comb(allele_count + ploidy - 1, ploidy)


def _parse_header_field(line: str) -> Optional[HeaderFieldDeclaration]:
    body_match = re.search(r"<(.*)>$", line)
    if body_match is None:
        return None
    body = body_match.group(1)
    values: Dict[str, str] = {}
    for match in re.finditer(r'(?:^|,)([^,=]+)=("[^"]*"|[^,]*)', body):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        values[key] = value
    field_id = values.get("ID")
    if not field_id:
        return None
    return HeaderFieldDeclaration(
        field_id=field_id,
        number=values.get("Number", ""),
        field_type=values.get("Type", ""),
        description=values.get("Description", ""),
    )


def _parse_contig(line: str) -> Optional[Tuple[str, Optional[int], Tuple[str, ...]]]:
    body_match = re.search(r"<(.*)>$", line)
    if body_match is None:
        return None
    body = body_match.group(1)
    values: Dict[str, str] = {}
    for match in re.finditer(r'(?:^|,)([^,=]+)=([^,]*)', body):
        values[match.group(1).strip()] = match.group(2).strip()
    contig_id = values.get("ID")
    if not contig_id:
        return None
    length_text = values.get("length")
    length: Optional[int] = None
    if length_text:
        try:
            length = int(length_text)
            if length <= 0:
                raise MalformedVcfHeaderError(
                    "contig length must be a positive integer"
                )
        except ValueError as error:
            raise MalformedVcfHeaderError(
                "contig length must be a positive integer"
            ) from error
    assembly_text = values.get("assembly")
    assembly_labels = (assembly_text,) if assembly_text else ()
    return contig_id, length, assembly_labels


def _parse_raw_info(raw_info: str) -> Tuple[InfoFieldEvidence, ...]:
    if raw_info in ("", "."):
        return ()
    entries: List[InfoFieldEvidence] = []
    for item in raw_info.split(";"):
        if not item:
            entries.append(InfoFieldEvidence(key="", raw_value=None))
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            entries.append(InfoFieldEvidence(key=key, raw_value=value))
        else:
            entries.append(InfoFieldEvidence(key=item, raw_value=None))
    return tuple(entries)


def _parse_gt(raw_gt: str, alt_count: int) -> Optional[GenotypeEvidence]:
    if "/" in raw_gt and "|" in raw_gt:
        return None
    separator: Optional[GenotypeSeparator] = None
    if "/" in raw_gt:
        separator = GenotypeSeparator.UNPHASED
        parts = raw_gt.split("/")
    elif "|" in raw_gt:
        separator = GenotypeSeparator.PHASED
        parts = raw_gt.split("|")
    else:
        parts = (raw_gt,)

    if not parts:
        return None
    alleles: List[Optional[int]] = []
    for part in parts:
        if part == ".":
            alleles.append(None)
            continue
        if not re.fullmatch(r"\d+", part):
            return None
        allele = int(part)
        if allele < 0 or allele > alt_count:
            return None
        alleles.append(allele)
    return GenotypeEvidence(
        allele_indices=tuple(alleles),
        separator=separator,
        is_missing=all(allele is None for allele in alleles),
        raw_gt=raw_gt,
    )


def _parse_integer(raw_value: str, allow_zero: bool = True) -> Optional[int]:
    if raw_value == ".":
        return None
    if not re.fullmatch(r"-?\d+", raw_value):
        return None
    value = int(raw_value)
    if value < 0 and not allow_zero:
        return None
    if value < 0:
        return None
    return value


def _issue(
    issue_id: str,
    category: VariantIssueCategory,
    line_number: int,
    reason: str,
    fixed_fields: Iterable[str],
    record_evidence: Optional[RecordIssueEvidence] = None,
    duplicate: Optional[DuplicateEvidence] = None,
    severity: IssueSeverity = IssueSeverity.ERROR,
) -> VariantIssue:
    fields = tuple(fixed_fields)
    if len(fields) < 5:
        fields = fields + ("",) * (5 - len(fields))
    return VariantIssue(
        issue_id=issue_id,
        category=category,
        severity=severity,
        line_number=line_number,
        reason=reason,
        raw_chrom=fields[0],
        raw_pos=fields[1],
        raw_ref=fields[3] if len(fields) > 3 else "",
        raw_alt=fields[4] if len(fields) > 4 else "",
        record=record_evidence,
        duplicate=duplicate,
    )


def _assembly_status(
    declarations: Tuple[AssemblyDeclaration, ...],
    contig_assembly_complete: bool,
) -> AssemblyStatus:
    reliable = tuple(
        declaration.raw_label
        for declaration in declarations
        if declaration.raw_label.strip()
    )
    distinct = tuple(dict.fromkeys(reliable))
    if len(distinct) > 1:
        return AssemblyStatus.EXPLICIT_CONFLICTING
    if len(distinct) == 1 and (
        contig_assembly_complete
        or any(
            declaration.source in ("##assembly", "##reference")
            for declaration in declarations
        )
    ):
        return AssemblyStatus.EXPLICIT_SINGLE
    if len(distinct) == 1:
        return AssemblyStatus.AMBIGUOUS
    if any(not declaration.raw_label.strip() for declaration in declarations):
        return AssemblyStatus.AMBIGUOUS
    return AssemblyStatus.UNKNOWN


def ingest_vcf(source_ref: Union[str, Path]) -> VcfIngestionResult:
    """Ingest one uncompressed VCF read-only and stream its records."""

    path = Path(source_ref)
    if not path.is_file():
        raise VcfSourceError("VCF source is not a readable file")
    if path.suffix.lower() not in (".vcf",):
        raise UnsupportedVcfFormatError("only uncompressed .vcf input is supported")

    digest = hashlib.sha256()
    size = path.stat().st_size
    info_fields: List[HeaderFieldDeclaration] = []
    format_fields: List[HeaderFieldDeclaration] = []
    contigs: List[ContigMetadata] = []
    contig_map: Dict[str, ContigMetadata] = {}
    assembly_declarations: List[AssemblyDeclaration] = []
    header_columns: Optional[Tuple[str, ...]] = None
    sample_ids: Tuple[str, ...] = ()
    vcf_version: Optional[str] = None
    first_non_bom_line = True

    accepted: List[ValidatedVariantRecord] = []
    issues: List[VariantIssue] = []
    duplicate_locations: Dict[
        Tuple[str, int, str, Tuple[str, ...]],
        Dict[str, Tuple],
    ] = {}

    with path.open("rb") as vcf_file:
        for line_number, raw_line in enumerate(vcf_file, start=1):
            digest.update(raw_line)
            if raw_line.startswith(b"\x1f\x8b"):
                raise UnsupportedVcfFormatError("gzip and BCF input is not supported")
            try:
                line = raw_line.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as error:
                raise MalformedVcfHeaderError("VCF source is not UTF-8/ASCII") from error
            if first_non_bom_line:
                first_non_bom_line = False
                if line.startswith("\ufeff"):
                    line = line.lstrip("\ufeff")
                if not line.startswith("##fileformat="):
                    raise MalformedVcfHeaderError(
                        "##fileformat must be the first VCF header declaration"
                    )
                fileformat = line.split("=", 1)[1].strip()
                version_match = VCF_FILEFORMAT_PATTERN.fullmatch(fileformat)
                if version_match is None:
                    raise MalformedVcfHeaderError(
                        "malformed VCF fileformat declaration: {}".format(fileformat)
                    )
                vcf_version = version_match.group(1)
                if vcf_version not in SUPPORTED_VCF_VERSIONS:
                    raise UnsupportedVcfFormatError(
                        "unsupported VCF version: {}".format(vcf_version)
                    )
                continue

            if line.startswith("##INFO=<"):
                declaration = _parse_header_field(line)
                if declaration is None:
                    raise MalformedVcfHeaderError("malformed INFO declaration")
                if not declaration.number or not declaration.field_type:
                    raise MalformedVcfHeaderError("INFO declaration is missing Number or Type")
                if any(field.field_id == declaration.field_id for field in info_fields):
                    raise MalformedVcfHeaderError(
                        "duplicate INFO declaration: {}".format(declaration.field_id)
                    )
                info_fields.append(declaration)
                continue
            if line.startswith("##FORMAT=<"):
                declaration = _parse_header_field(line)
                if declaration is None:
                    raise MalformedVcfHeaderError("malformed FORMAT declaration")
                if not declaration.number or not declaration.field_type:
                    raise MalformedVcfHeaderError("FORMAT declaration is missing Number or Type")
                if any(field.field_id == declaration.field_id for field in format_fields):
                    raise MalformedVcfHeaderError(
                        "duplicate FORMAT declaration: {}".format(declaration.field_id)
                    )
                format_fields.append(declaration)
                continue
            if line.startswith("##contig=<"):
                parsed_contig = _parse_contig(line)
                if parsed_contig is None:
                    raise MalformedVcfHeaderError("malformed contig declaration")
                contig_id, length, assembly_labels = parsed_contig
                contig = ContigMetadata(
                    contig_id=contig_id,
                    length=length,
                    assembly_labels=assembly_labels,
                )
                contigs.append(contig)
                contig_map[contig_id] = contig
                for assembly_label in assembly_labels:
                    assembly_declarations.append(
                        AssemblyDeclaration(
                            raw_label=assembly_label,
                            source="contig={}".format(contig_id),
                        )
                    )
                continue
            if line.startswith("##assembly="):
                raw_label = line.split("=", 1)[1].strip()
                assembly_declarations.append(
                    AssemblyDeclaration(raw_label=raw_label, source="##assembly")
                )
                continue
            if line.startswith("##reference="):
                continue
            if line.startswith("#"):
                if line.startswith("#CHROM\t"):
                    columns = tuple(line.lstrip("#").split("\t"))
                    if len(columns) < 8:
                        raise MalformedVcfHeaderError("missing required fixed columns")
                    if tuple(columns[:8]) != FIXED_COLUMNS:
                        raise MalformedVcfHeaderError(
                            "required fixed columns are missing or out of order"
                        )
                    if len(columns) == 8:
                        sample_ids = ()
                    elif len(columns) == 9:
                        raise MalformedVcfHeaderError(
                            "FORMAT without sample columns is malformed"
                        )
                    else:
                        if columns[8] != "FORMAT":
                            raise MalformedVcfHeaderError(
                                "column 9 must be FORMAT when samples are present"
                            )
                        sample_ids = tuple(columns[9:])
                        if any(not sample or len(set(sample_ids)) != len(sample_ids)
                               for sample in sample_ids):
                            raise MalformedVcfHeaderError(
                                "sample IDs must be non-empty and unique"
                            )
                    header_columns = columns
                continue

            if header_columns is None:
                raise MalformedVcfHeaderError("data record found before #CHROM")

            fields = line.split("\t")
            minimum = 8
            if len(fields) < minimum:
                fixed_issue = tuple(fields + ("",) * (minimum - len(fields)))
                issues.append(
                    _issue(
                        "line-{}-malformed".format(line_number),
                        VariantIssueCategory.MALFORMED_RECORD,
                        line_number,
                        "record has fewer than eight fixed fields",
                        fixed_issue,
                        RecordIssueEvidence(
                            line_number=line_number,
                            fixed_fields=fixed_issue,
                            raw_format=(),
                            raw_samples=(),
                            sample_ids=sample_ids,
                            raw_info=(),
                        ),
                    )
                )
                continue

            chrom, pos_text, record_id, ref, alt_text, qual_text, filter_text, raw_info = fields[:8]
            fixed_fields = tuple(fields[:8])

            failures: List[str] = []
            try:
                pos = int(pos_text)
                if pos < 1:
                    failures.append("POS must be a positive integer")
            except ValueError:
                pos = 0
                failures.append("POS must be a positive integer")

            contig = contig_map.get(chrom)
            if contig is not None and contig.length is not None and pos > contig.length:
                failures.append("POS exceeds declared contig length")
            elif contig is None and contigs:
                failures.append("CHROM is absent from declared contigs")

            if not NUCLEOTIDE_PATTERN.fullmatch(ref):
                failures.append("REF contains unsupported nucleotide evidence")

            alt_tokens = () if alt_text == "." else tuple(alt_text.split(","))
            if alt_tokens:
                if len(set(alt_tokens)) != len(alt_tokens):
                    failures.append("ALT contains duplicate alleles")
                for alt in alt_tokens:
                    if (
                        alt == ""
                        or alt.startswith("<")
                        or alt.startswith("*")
                        or "[" in alt
                        or "]" in alt
                        or not NUCLEOTIDE_PATTERN.fullmatch(alt)
                    ):
                        failures.append("symbolic or breakend ALT evidence is unsupported")

            qual: Optional[float] = None
            raw_qual = None if qual_text == "." else qual_text
            if qual_text != ".":
                try:
                    qual = float(qual_text)
                    if not math.isfinite(qual) or qual < 0:
                        failures.append("QUAL must be finite and non-negative")
                except ValueError:
                    failures.append("QUAL must be numeric or missing")

            filters = () if filter_text in ("", ".") else tuple(filter_text.split(";"))
            raw_info_entries = _parse_raw_info(raw_info)
            if raw_info not in ("", "."):
                info_declaration_map = {
                    declaration.field_id: declaration
                    for declaration in info_fields
                }
                for entry in raw_info_entries:
                    if not entry.key:
                        failures.append("INFO contains an empty key")
                        continue
                    declaration = info_declaration_map.get(entry.key)
                    if declaration is not None and declaration.number == "0":
                        if entry.raw_value is not None:
                            failures.append(
                                "INFO flag {} must not have a value".format(entry.key)
                            )
                    elif declaration is not None and entry.raw_value is None:
                        failures.append(
                            "INFO field {} requires a value".format(entry.key)
                        )

            raw_format: Tuple[str, ...] = ()
            raw_samples: Tuple[str, ...] = tuple(fields[9:]) if len(fields) >= 9 else ()
            sample_values: Tuple[str, ...] = ()
            samples: List[SampleGenotypeEvidence] = []

            if len(fields) >= 9:
                if not sample_ids:
                    failures.append("record has FORMAT/sample columns but #CHROM declares none")
                else:
                    raw_format = tuple(fields[8].split(":"))
                    sample_values = tuple(fields[9:])
                    if len(sample_values) != len(sample_ids):
                        failures.append("sample column count does not match #CHROM")
            elif sample_ids:
                failures.append("record lacks FORMAT for sample columns")

            format_declaration_map = {
                declaration.field_id: declaration
                for declaration in format_fields
            }
            if raw_format:
                if len(set(raw_format)) != len(raw_format):
                    failures.append("FORMAT contains duplicate field IDs")

            for sample_index, sample_id in enumerate(sample_ids):
                if sample_index >= len(sample_values):
                    raw_sample = ""
                else:
                    raw_sample = sample_values[sample_index]
                raw_field_values = raw_sample.split(":") if raw_sample else ()
                if len(raw_format) != len(raw_field_values):
                    failures.append(
                        "FORMAT and sample field counts differ for sample {}".format(sample_id)
                    )
                    raw_field_values = raw_field_values + ("",) * max(
                        0, len(raw_format) - len(raw_field_values)
                    )
                value_map = {
                    key: value
                    for key, value in zip(raw_format, raw_field_values)
                }
                format_evidence = tuple(
                    FormatFieldEvidence(key=key, raw_value=value_map.get(key, "."))
                    for key in raw_format
                )

                gt: Optional[GenotypeEvidence] = None
                dp: Optional[int] = None
                gq: Optional[int] = None
                ad: Optional[Tuple[Optional[int], ...]] = None
                pl: Optional[Tuple[Optional[int], ...]] = None

                if "GT" in value_map:
                    declaration = format_declaration_map.get("GT")
                    if declaration is None:
                        failures.append("GT declaration is missing")
                    elif declaration.field_type != "String" or declaration.number != "1":
                        failures.append("GT declaration must be String/Number=1")
                    gt = _parse_gt(value_map["GT"], len(alt_tokens))
                    if gt is None:
                        failures.append("GT is invalid or has mixed/malformed separators")

                if "DP" in value_map:
                    declaration = format_declaration_map.get("DP")
                    if declaration is None:
                        failures.append("DP declaration is missing")
                    elif (
                        declaration.field_type != "Integer"
                        or declaration.number != "1"
                    ):
                        failures.append("DP declaration must be Integer/Number=1")
                    dp = _parse_integer(value_map["DP"], allow_zero=True)
                    if value_map["DP"] != "." and dp is None:
                        failures.append("DP must be a non-negative integer or missing")

                if "GQ" in value_map:
                    declaration = format_declaration_map.get("GQ")
                    if declaration is None:
                        failures.append("GQ declaration is missing")
                    elif (
                        declaration.field_type != "Integer"
                        or declaration.number != "1"
                    ):
                        failures.append("GQ declaration must be Integer/Number=1")
                    gq = _parse_integer(value_map["GQ"], allow_zero=True)
                    if value_map["GQ"] != "." and gq is None:
                        failures.append("GQ must be a non-negative integer or missing")

                if "AD" in value_map:
                    declaration = format_declaration_map.get("AD")
                    if declaration is None:
                        failures.append("AD declaration is missing")
                    elif (
                        declaration.field_type != "Integer"
                        or declaration.number != "R"
                    ):
                        failures.append("AD declaration must be Integer/Number=R")
                    raw_ad = value_map["AD"]
                    ad_parts = () if raw_ad == "." else tuple(raw_ad.split(","))
                    ad_values: List[Optional[int]] = []
                    ad_valid = True
                    if raw_ad == ".":
                        ad = None
                    else:
                        for raw_part in ad_parts:
                            if raw_part == ".":
                                ad_values.append(None)
                            elif re.fullmatch(r"\d+", raw_part):
                                ad_values.append(int(raw_part))
                            else:
                                ad_valid = False
                                ad_values.append(None)
                        if len(ad_values) != 1 + len(alt_tokens):
                            failures.append("AD cardinality must be R")
                        ad = tuple(ad_values) if ad_valid else None
                        if not ad_valid:
                            failures.append("AD contains malformed integer values")

                if "PL" in value_map:
                    declaration = format_declaration_map.get("PL")
                    if declaration is None:
                        failures.append("PL declaration is missing")
                    elif (
                        declaration.field_type != "Integer"
                        or declaration.number != "G"
                    ):
                        failures.append("PL declaration must be Integer/Number=G")
                    raw_pl = value_map["PL"]
                    if raw_pl == ".":
                        pl = None
                    else:
                        pl_parts = tuple(raw_pl.split(","))
                        pl_values: List[Optional[int]] = []
                        pl_valid = True
                        for raw_part in pl_parts:
                            if raw_part == ".":
                                pl_values.append(None)
                            elif re.fullmatch(r"\d+", raw_part):
                                pl_values.append(int(raw_part))
                            else:
                                pl_valid = False
                                pl_values.append(None)
                        if not pl_valid:
                            failures.append("PL contains malformed integer values")
                        else:
                            pl = tuple(pl_values)
                            if gt is not None:
                                expected = pl_cardinality(
                                    1 + len(alt_tokens),
                                    len(gt.allele_indices),
                                )
                                if len(pl_values) != expected:
                                    failures.append(
                                        "PL cardinality must be C(a+p-1,p)"
                                    )
                            else:
                                issues.append(
                                    _issue(
                                        "line-{}-pl-unverifiable-{}".format(
                                            line_number, sample_id
                                        ),
                                        VariantIssueCategory.UNVERIFIABLE_PL,
                                        line_number,
                                        "PL cardinality is unverifiable because GT is absent",
                                        fixed_fields,
                                        severity=IssueSeverity.WARNING,
                                    )
                                )

                typed_format = []
                for key in raw_format:
                    raw_value = value_map.get(key, ".")
                    typed_value: object = None
                    if key == "DP":
                        typed_value = dp
                    elif key == "GQ":
                        typed_value = gq
                    elif key == "AD":
                        typed_value = ad
                    elif key == "PL":
                        typed_value = pl
                    typed_format.append(
                        FormatFieldEvidence(
                            key=key,
                            raw_value=raw_value,
                            typed_value=typed_value,
                        )
                    )

                samples.append(
                    SampleGenotypeEvidence(
                        sample_id=sample_id,
                        sample_column=9 + sample_index,
                        gt=gt,
                        format_fields=tuple(typed_format),
                        dp=dp,
                        gq=gq,
                        ad=ad,
                        pl=pl,
                    )
                )

            if "GT" not in raw_format and not raw_format:
                pass

            if len(fields) == 8 and sample_ids:
                failures.append("FORMAT/sample columns are missing")

            if failures:
                record_evidence = RecordIssueEvidence(
                    line_number=line_number,
                    fixed_fields=fixed_fields,
                    raw_format=raw_format,
                    raw_samples=raw_samples,
                    sample_ids=sample_ids,
                    raw_info=raw_info_entries,
                )
                unsupported_evidence = any(
                    "unsupported" in failure.lower()
                    or "symbolic" in failure.lower()
                    or "breakend" in failure.lower()
                    for failure in failures
                )
                issues.append(
                    _issue(
                        "line-{}-malformed".format(line_number),
                        (
                            VariantIssueCategory.UNSUPPORTED_ALLELE
                            if unsupported_evidence
                            else VariantIssueCategory.MALFORMED_RECORD
                        ),
                        line_number,
                        "; ".join(dict.fromkeys(failures)),
                        fixed_fields,
                        record_evidence,
                    )
                )
                continue

            source_record_id = "{}#line={}".format(path, line_number)
            id_values = () if record_id == "." else tuple(record_id.split(";"))
            record = ValidatedVariantRecord(
                source_record_id=source_record_id,
                line_number=line_number,
                chrom=chrom,
                pos=pos,
                id_values=id_values,
                ref=ref,
                alt=alt_tokens,
                qual=qual,
                raw_qual=raw_qual,
                filters=filters,
                raw_info=raw_info_entries,
                raw_format=raw_format,
                samples=tuple(samples),
                raw_fixed_fields=fixed_fields,
            )

            duplicate_key = (chrom, pos, ref, alt_tokens)
            location = duplicate_locations.get(duplicate_key)
            if location is None:
                duplicate_locations[duplicate_key] = {
                    "line_numbers": (line_number,),
                    "source_record_ids": (source_record_id,),
                }
            else:
                line_numbers = location["line_numbers"] + (line_number,)
                source_record_ids = location["source_record_ids"] + (source_record_id,)
                duplicate_locations[duplicate_key] = {
                    "line_numbers": line_numbers,
                    "source_record_ids": source_record_ids,
                }
                duplicate = DuplicateEvidence(
                    duplicate_key=duplicate_key,
                    line_numbers=line_numbers,
                    source_record_ids=source_record_ids,
                )
                issues.append(
                    _issue(
                        "line-{}-duplicate".format(line_number),
                        VariantIssueCategory.DUPLICATE_RECORD,
                        line_number,
                        "whole-file duplicate record; both original records are preserved",
                        fixed_fields,
                        duplicate=duplicate,
                        severity=IssueSeverity.WARNING,
                    )
                )

            accepted.append(record)

    if vcf_version is None:
        raise MalformedVcfHeaderError("missing ##fileformat")

    all_contigs_have_assembly = bool(contigs) and all(
        contig.assembly_labels for contig in contigs
    )
    assembly_status = _assembly_status(
        tuple(assembly_declarations),
        all_contigs_have_assembly,
    )
    if assembly_status == AssemblyStatus.EXPLICIT_CONFLICTING:
        labels = tuple(
            declaration.raw_label
            for declaration in assembly_declarations
            if declaration.raw_label.strip()
        )
        raise AssemblyDeclarationError(
            "conflicting assembly labels: {}".format(
                ", ".join(dict.fromkeys(labels))
            )
        )

    source = VcfSourceIdentity(
        source_ref=str(path),
        sha256=digest.hexdigest(),
        size_bytes=size,
        vcf_format="VCF",
    )
    metadata = VcfMetadata(
        source=source,
        vcf_version=vcf_version,
        assembly_status=assembly_status,
        assembly_declarations=tuple(assembly_declarations),
        contigs=tuple(contigs),
        sample_ids=sample_ids,
        info_fields=tuple(info_fields),
        format_fields=tuple(format_fields),
    )

    if (
        assembly_status == AssemblyStatus.UNKNOWN
        or assembly_status == AssemblyStatus.AMBIGUOUS
    ):
        issues.append(
            _issue(
                "line-0-assembly",
                VariantIssueCategory.ASSEMBLY_AMBIGUITY,
                0,
                "assembly status prevents compatibility inference",
                ("", "", "", "", ""),
                severity=IssueSeverity.WARNING,
            )
        )

    return VcfIngestionResult(
        metadata=metadata,
        accepted_records=tuple(accepted),
        issues=tuple(issues),
    )
