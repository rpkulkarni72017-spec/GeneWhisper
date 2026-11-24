"""Synthetic structural VCF validation tests."""

import tempfile
import unittest
from pathlib import Path

from app.genomics.exceptions import (
    MalformedVcfHeaderError,
    UnsupportedVcfFormatError,
)
from app.genomics.vcf_validation import ingest_vcf, pl_cardinality


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")


class VcfValidationTests(unittest.TestCase):
    def write_vcf(self, text, name="synthetic.vcf"):
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="stage5b-vcf-",
            dir=str(PROJECT_ROOT),
        )
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / name
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def test_supported_header_and_eight_column_record(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=1,length=100>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t10\t.\tA\tC\t30\tPASS\t.\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.metadata.vcf_version, "4.2")
        self.assertEqual(len(result.accepted_records), 1)
        self.assertEqual(result.accepted_records[0].samples, ())
        self.assertEqual(result.accepted_records[0].raw_format, ())

    def test_uncompressed_versions_and_gzip_extension(self):
        for version in ("4.1", "4.2", "4.3"):
            path = self.write_vcf(
                "##fileformat=VCFv{}\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                "1\t1\t.\tA\tC\t.\tPASS\t.\n".format(version),
                "version-{}.vcf".format(version),
            )
            self.assertEqual(ingest_vcf(path).metadata.vcf_version, version)

        gzip_path = self.write_vcf(
            "##fileformat=VCFv4.2\n",
            "compressed.vcf.gz",
        )
        with self.assertRaises(UnsupportedVcfFormatError):
            ingest_vcf(gzip_path)

    def test_unsupported_and_malformed_fileformat_values(self):
        unsupported = self.write_vcf(
            "##fileformat=VCFv4.5\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
        with self.assertRaises(UnsupportedVcfFormatError):
            ingest_vcf(unsupported)

        malformed = self.write_vcf(
            "##fileformat=VCF4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
        with self.assertRaises(MalformedVcfHeaderError):
            ingest_vcf(malformed)

    def test_unsupported_symbolic_and_breakend_alleles_are_issues(self):
        for alt in ("<DEL>", "A]chr2:20]"):
            path = self.write_vcf(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                "1\t10\t.\tA\t{}\t.\tPASS\t.\n".format(alt)
            )
            result = ingest_vcf(path)
            self.assertEqual(result.accepted_records, ())
            self.assertTrue(result.issues)

    def test_pl_cardinality_formula(self):
        self.assertEqual(pl_cardinality(2, 1), 2)
        self.assertEqual(pl_cardinality(3, 1), 3)
        self.assertEqual(pl_cardinality(2, 2), 3)
        self.assertEqual(pl_cardinality(3, 2), 6)
        self.assertEqual(pl_cardinality(2, 3), 4)
        self.assertEqual(pl_cardinality(3, 3), 10)

    def test_absent_gt_preserves_pl_as_unverifiable(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC\t.\tPASS\t.\tPL\t3,2,1\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        self.assertTrue(
            any(issue.category.value == "unverifiable_pl" for issue in result.issues)
        )

    def test_invalid_pl_cardinality_rejects_complete_record(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC,G\t.\tPASS\t.\tGT:PL\t0/1:3,2\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.accepted_records, ())
        self.assertTrue(result.issues)
        self.assertEqual(result.issues[0].record.fixed_fields[0], "1")

    def test_missing_allele_gt_still_derives_ploidy(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC\t.\tPASS\t.\tGT:PL\t./.:3,2,1\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        gt = result.accepted_records[0].samples[0].gt
        self.assertEqual(gt.allele_indices, (None, None))
        self.assertEqual(len(gt.allele_indices), 2)

    def test_header_only_and_zero_record_files_are_valid(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.3\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.accepted_records, ())
        self.assertEqual(result.metadata.vcf_version, "4.3")

    def test_duplicate_info_keys_preserve_order(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t10\t.\tA\tC\t.\tPASS\tX=first;X=second\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        self.assertEqual(
            tuple(
                (entry.key, entry.raw_value)
                for entry in result.accepted_records[0].raw_info
            ),
            (("X", "first"), ("X", "second")),
        )


if __name__ == "__main__":
    unittest.main()
