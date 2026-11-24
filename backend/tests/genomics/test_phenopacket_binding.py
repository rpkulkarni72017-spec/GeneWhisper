"""Synthetic future Phenopacket/VCF binding contract tests."""

import unittest

from app.genomics.assembly import AssemblyCompatibility
from app.genomics.phenopacket_binding import PhenopacketVariantBinding


class PhenopacketBindingTests(unittest.TestCase):
    def test_binding_preserves_identity_and_unresolved_assembly(self):
        binding = PhenopacketVariantBinding(
            case_id="synthetic-case",
            phenopacket_id="synthetic-phenopacket",
            subject_id="synthetic-subject",
            hts_uri="synthetic.vcf",
            hts_sha256="0" * 64,
            vcf_sample_id="synthetic-sample",
            phenopacket_assembly="hg19",
            vcf_assembly_status="explicit_single",
            compatibility=AssemblyCompatibility.UNRESOLVED,
        )
        self.assertEqual(binding.phenopacket_assembly, "hg19")
        self.assertEqual(binding.vcf_assembly_status, "explicit_single")
        self.assertEqual(binding.compatibility, AssemblyCompatibility.UNRESOLVED)


if __name__ == "__main__":
    unittest.main()
