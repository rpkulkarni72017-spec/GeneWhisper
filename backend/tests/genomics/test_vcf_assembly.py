"""Synthetic strict assembly handling tests."""

import tempfile
import unittest
from pathlib import Path

from app.genomics.assembly import AssemblyStatus
from app.genomics.exceptions import AssemblyDeclarationError
from app.genomics.vcf_validation import ingest_vcf


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")


class VcfAssemblyTests(unittest.TestCase):
    def write_vcf(self, text):
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="stage5b-assembly-",
            dir=str(PROJECT_ROOT),
        )
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "synthetic.vcf"
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def test_unknown_assembly(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t1\t.\tA\tC\t.\tPASS\t.\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.metadata.assembly_status, AssemblyStatus.UNKNOWN)

    def test_explicit_single_assembly_is_preserved(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##assembly=hg19\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t1\t.\tA\tC\t.\tPASS\t.\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.metadata.assembly_status, AssemblyStatus.EXPLICIT_SINGLE)
        self.assertEqual(
            result.metadata.assembly_declarations[0].raw_label,
            "hg19",
        )

    def test_conflicting_sourced_labels_are_fatal(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##assembly=hg19\n"
            "##contig=<ID=1,assembly=b37,length=100>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t1\t.\tA\tC\t.\tPASS\t.\n"
        )
        with self.assertRaises(AssemblyDeclarationError):
            ingest_vcf(path)

    def test_incomplete_assembly_is_ambiguous(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##assembly=\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t1\t.\tA\tC\t.\tPASS\t.\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.metadata.assembly_status, AssemblyStatus.AMBIGUOUS)


if __name__ == "__main__":
    unittest.main()
