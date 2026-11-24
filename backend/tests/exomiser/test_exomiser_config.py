"""Synthetic Exomiser configuration and YAML tests."""

import json
import unittest

from app.exomiser.binding import ExomiserCaseBinding
from app.exomiser.config import (
    ExomiserInvocationConfig,
    build_analysis_yaml,
    build_output_yaml,
)
from app.genomics.assembly import AssemblyStatus


class ExomiserConfigTests(unittest.TestCase):
    def setUp(self):
        self.binding = ExomiserCaseBinding(
            case_id="synthetic-case",
            phenotype_snapshot_id="snapshot-1",
            phenopacket_id="synthetic-phenopacket",
            subject_id="synthetic-subject",
            vcf_source_ref="synthetic.vcf",
            vcf_sha256="a" * 64,
            vcf_sample_id="synthetic-sample",
            phenopacket_assembly="hg19",
            vcf_assembly_status=AssemblyStatus.EXPLICIT_SINGLE,
            vcf_assembly_labels=("hg19",),
        )
        self.config = ExomiserInvocationConfig(
            exomiser_version="15.1.0",
            exomiser_distribution_sha256="b" * 64,
            java_executable="java",
            jar_path="C:\\synthetic\\exomiser-cli-15.1.0.jar",
            application_properties="C:\\synthetic\\application.properties",
            data_directory="C:\\synthetic\\data",
            hg19_data_version="2512",
            hg38_data_version=None,
            phenotype_data_version="2512",
            reference_data_identity="synthetic-reference-data",
            reference_data_sha256="c" * 64,
            assembly="hg19",
            timeout_seconds=60,
            max_output_bytes=1024,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )

    def test_analysis_and_output_yaml_are_valid_json_yaml(self):
        analysis = json.loads(
            build_analysis_yaml(
                binding=self.binding,
                hpo_ids=("HP:0000118",),
                vcf_path="synthetic.vcf",
                output_directory="synthetic-output",
            )
        )
        output = json.loads(
            build_output_yaml(
                output_directory="synthetic-output",
                output_filename="synthetic-result",
            )
        )
        self.assertEqual(analysis["analysis"]["genomeAssembly"], "hg19")
        self.assertEqual(analysis["analysis"]["proband"], "synthetic-sample")
        self.assertEqual(output["outputFormats"], ["TSV_GENE", "TSV_VARIANT"])

    def test_unsupported_exomiser_version_is_rejected(self):
        values = dict(
            exomiser_version="15.0.0",
            exomiser_distribution_sha256="b" * 64,
            java_executable="java",
            jar_path="synthetic.jar",
            application_properties="synthetic.properties",
            data_directory="synthetic-data",
            hg19_data_version="2512",
            hg38_data_version=None,
            phenotype_data_version="2512",
            reference_data_identity="synthetic",
            reference_data_sha256="c" * 64,
            assembly="hg19",
            timeout_seconds=60,
            max_output_bytes=1024,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )
        with self.assertRaises(ValueError):
            ExomiserInvocationConfig(**values)


if __name__ == "__main__":
    unittest.main()
