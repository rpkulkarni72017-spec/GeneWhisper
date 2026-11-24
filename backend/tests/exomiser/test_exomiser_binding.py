"""Synthetic Exomiser binding tests."""

import unittest
from types import SimpleNamespace

from app.exomiser.binding import ExomiserCaseBinding, validate_exomiser_binding
from app.exomiser.exceptions import ExomiserBindingError
from app.genomics.assembly import AssemblyStatus


class ExomiserBindingTests(unittest.TestCase):
    def setUp(self):
        self.metadata = SimpleNamespace(
            sample_ids=("synthetic-sample",),
            source=SimpleNamespace(sha256="a" * 64),
        )
        self.binding = ExomiserCaseBinding(
            case_id="synthetic-case",
            phenotype_snapshot_id="synthetic-snapshot",
            phenopacket_id="synthetic-phenopacket",
            subject_id="synthetic-subject",
            vcf_source_ref="synthetic.vcf",
            vcf_sha256="a" * 64,
            vcf_sample_id="synthetic-sample",
            phenopacket_assembly="hg19",
            vcf_assembly_status=AssemblyStatus.EXPLICIT_SINGLE,
            vcf_assembly_labels=("hg19",),
        )

    def test_explicit_sample_and_subject_are_validated(self):
        snapshot = SimpleNamespace(subject_id="synthetic-subject")
        self.assertIs(
            validate_exomiser_binding(
                self.binding,
                vcf_metadata=self.metadata,
                phenotype_snapshot=snapshot,
                assembly_mapper=SimpleNamespace(
                    compatible=lambda **kwargs: __import__(
                        "app.exomiser.assembly",
                        fromlist=["AssemblyCompatibility"],
                    ).AssemblyCompatibility.COMPATIBLE
                ),
            ),
            self.binding,
        )

    def test_missing_multi_sample_selection_is_rejected(self):
        metadata = SimpleNamespace(
            sample_ids=("s1", "s2"),
            source=SimpleNamespace(sha256="a" * 64),
        )
        binding = ExomiserCaseBinding(
            case_id="synthetic-case",
            phenotype_snapshot_id="synthetic-snapshot",
            phenopacket_id="synthetic-phenopacket",
            subject_id="synthetic-subject",
            vcf_source_ref="synthetic.vcf",
            vcf_sha256="a" * 64,
            vcf_sample_id="",
            phenopacket_assembly="hg19",
            vcf_assembly_status=AssemblyStatus.EXPLICIT_SINGLE,
            vcf_assembly_labels=("hg19",),
        )
        with self.assertRaises(ExomiserBindingError):
            validate_exomiser_binding(binding, vcf_metadata=metadata)

    def test_default_mapper_never_assumes_assembly_equivalence(self):
        with self.assertRaises(ExomiserBindingError):
            validate_exomiser_binding(self.binding, vcf_metadata=self.metadata)


if __name__ == "__main__":
    unittest.main()
