"""Synthetic Exomiser preflight tests."""

import unittest
from types import SimpleNamespace

from app.exomiser.assembly import AssemblyCompatibility
from app.exomiser.preflight import preflight_exomiser
from app.exomiser.exceptions import ExomiserPreflightError


class ExomiserPreflightTests(unittest.TestCase):
    def make_inputs(self):
        binding = SimpleNamespace(
            case_id="synthetic-case",
            phenotype_snapshot_id="snapshot-1",
            subject_id="synthetic-subject",
            vcf_sha256="a" * 64,
            vcf_sample_id="synthetic-sample",
        )
        phenotype = SimpleNamespace(
            phenotype_snapshot_id="snapshot-1",
            subject_id="synthetic-subject",
            positive_hpo_ids=("HP:0000118",),
        )
        vcf = SimpleNamespace(
            metadata=SimpleNamespace(
                source=SimpleNamespace(sha256="a" * 64),
                sample_ids=("synthetic-sample",),
                assembly_status=__import__(
                    "app.genomics.assembly",
                    fromlist=["AssemblyStatus"],
                ).AssemblyStatus.EXPLICIT_SINGLE,
            ),
            issues=(),
        )
        config = SimpleNamespace()
        return binding, phenotype, vcf, config

    def test_compatible_preflight_passes(self):
        binding, phenotype, vcf, config = self.make_inputs()
        report = preflight_exomiser(
            binding=binding,
            vcf_ingestion=vcf,
            phenotype_snapshot=phenotype,
            config=config,
            assembly_compatibility=AssemblyCompatibility.COMPATIBLE,
        )
        self.assertTrue(report.can_execute)

    def test_unresolved_assembly_blocks_execution(self):
        binding, phenotype, vcf, config = self.make_inputs()
        with self.assertRaises(ExomiserPreflightError):
            preflight_exomiser(
                binding=binding,
                vcf_ingestion=vcf,
                phenotype_snapshot=phenotype,
                config=config,
                assembly_compatibility=AssemblyCompatibility.UNRESOLVED,
            )

    def test_stale_snapshot_blocks_execution(self):
        binding, phenotype, vcf, config = self.make_inputs()
        phenotype = SimpleNamespace(
            phenotype_snapshot_id="older-snapshot",
            subject_id="synthetic-subject",
            positive_hpo_ids=("HP:0000118",),
        )
        with self.assertRaises(ExomiserPreflightError):
            preflight_exomiser(
                binding=binding,
                vcf_ingestion=vcf,
                phenotype_snapshot=phenotype,
                config=config,
                assembly_compatibility=AssemblyCompatibility.COMPATIBLE,
            )


if __name__ == "__main__":
    unittest.main()
