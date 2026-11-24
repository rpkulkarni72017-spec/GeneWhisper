"""Immutable Exomiser 15.1.0 configuration and YAML writers."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .exceptions import ExomiserConfigurationError


@dataclass(frozen=True)
class ExomiserInvocationConfig:
    exomiser_version: str
    exomiser_distribution_sha256: str
    java_executable: str
    jar_path: str
    application_properties: str
    data_directory: str
    hg19_data_version: Optional[str]
    hg38_data_version: Optional[str]
    phenotype_data_version: str
    reference_data_identity: str
    reference_data_sha256: str
    assembly: str
    timeout_seconds: int
    max_output_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int

    def __post_init__(self):
        for name, value in (
            ("java_executable", self.java_executable),
            ("jar_path", self.jar_path),
            ("application_properties", self.application_properties),
            ("data_directory", self.data_directory),
            ("reference_data_identity", self.reference_data_identity),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ExomiserConfigurationError("{} is required".format(name))
        if self.exomiser_version != "15.1.0":
            raise ExomiserConfigurationError(
                "only Exomiser 15.1.0 is supported"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.exomiser_distribution_sha256):
            raise ExomiserConfigurationError(
                "Exomiser distribution SHA-256 must be lowercase hexadecimal"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.reference_data_sha256):
            raise ExomiserConfigurationError(
                "reference-data SHA-256 must be lowercase hexadecimal"
            )
        if self.timeout_seconds <= 0:
            raise ExomiserConfigurationError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ExomiserConfigurationError("max_output_bytes must be positive")
        if self.max_stdout_bytes <= 0 or self.max_stderr_bytes <= 0:
            raise ExomiserConfigurationError(
                "stdout and stderr limits must be positive"
            )
        if self.assembly not in ("hg19", "hg38"):
            raise ExomiserConfigurationError(
                "Exomiser assembly must be explicitly hg19 or hg38"
            )


def build_analysis_yaml(
    *,
    binding,
    hpo_ids,
    vcf_path,
    output_directory,
    analysis_mode="PASS_ONLY",
):
    if not hpo_ids:
        raise ExomiserConfigurationError(
            "at least one confirmed-positive HPO ID is required"
        )
    for hpo_id in hpo_ids:
        if not re.fullmatch(r"HP:\d+", hpo_id):
            raise ExomiserConfigurationError(
                "invalid HPO identifier: {}".format(hpo_id)
            )
    document = {
        "analysis": {
            "genomeAssembly": binding.phenopacket_assembly,
            "vcf": vcf_path,
            "ped": None,
            "proband": binding.vcf_sample_id,
            "hpoIds": list(hpo_ids),
            "analysisMode": analysis_mode,
        },
        "outputOptions": {
            "outputDirectory": output_directory,
            "outputContributingVariantsOnly": False,
            "numGenes": 0,
            "outputFormats": ["TSV_GENE", "TSV_VARIANT"],
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def build_output_yaml(
    *,
    output_directory,
    output_filename,
):
    if not output_directory or not output_filename:
        raise ExomiserConfigurationError(
            "output directory and filename are required"
        )
    document = {
        "outputContributingVariantsOnly": False,
        "numGenes": 0,
        "outputDirectory": output_directory,
        "outputFileName": output_filename,
        "outputFormats": ["TSV_GENE", "TSV_VARIANT"],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
