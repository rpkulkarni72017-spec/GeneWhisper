"""Synthetic multi-sample GT and FORMAT tests."""

import tempfile
import unittest
from pathlib import Path

from app.genomics.vcf_validation import ingest_vcf


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")


class VcfSampleTests(unittest.TestCase):
    def write_vcf(self, text):
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="stage5b-samples-",
            dir=str(PROJECT_ROOT),
        )
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "synthetic.vcf"
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def test_multi_sample_order_and_per_sample_evidence(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.3\n"
            "##contig=<ID=1,length=100>\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"DP\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts2\ts1\n"
            "1\t10\t.\tA\tC\t.\tPASS\t.\tGT:DP\t0|1:12\t./.:0\n"
        )
        result = ingest_vcf(path)
        record = result.accepted_records[0]
        self.assertEqual(tuple(sample.sample_id for sample in record.samples), ("s2", "s1"))
        self.assertEqual(record.samples[0].gt.separator.value, "|")
        self.assertEqual(record.samples[0].dp, 12)
        self.assertEqual(record.samples[1].dp, 0)

    def test_mixed_gt_separators_reject_complete_record(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC,G\t.\tPASS\t.\tGT\t0/1|2\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(result.accepted_records, ())
        self.assertTrue(result.issues)

    def test_gt_missing_allele_and_pl_validation(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2\n"
            "1\t10\t.\tA\tC\t.\tPASS\t.\tGT:PL\t.|.:.\t./.:3,2,1\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        self.assertEqual(result.accepted_records[0].samples[0].gt.allele_indices, (None, None))

    def test_dp_gq_ad_and_pl_declarations(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"DP\">\n"
            "##FORMAT=<ID=GQ,Number=1,Type=Integer,Description=\"GQ\">\n"
            "##FORMAT=<ID=AD,Number=R,Type=Integer,Description=\"AD\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC,G\t.\tPASS\t.\tGT:DP:GQ:AD:PL\t0/1:0:20:5,3,1:4,3,2,1,0,1\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        sample = result.accepted_records[0].samples[0]
        self.assertEqual(sample.dp, 0)
        self.assertEqual(sample.gq, 20)
        self.assertEqual(sample.ad, (5, 3, 1))
        self.assertEqual(len(sample.pl), 6)

    def test_missing_ad_and_pl_entries_are_preserved(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=AD,Number=R,Type=Integer,Description=\"AD\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC\t.\tPASS\t.\tGT:AD:PL\t./.:5,.:3,.,1\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        sample = result.accepted_records[0].samples[0]
        self.assertEqual(sample.ad, (5, None))
        self.assertEqual(sample.pl, (3, None, 1))

    def test_triploid_gt_uses_expected_pl_cardinality(self):
        path = self.write_vcf(
            "##fileformat=VCFv4.2\n"
            "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"GT\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"PL\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
            "1\t10\t.\tA\tC,G\t.\tPASS\t.\tGT:PL\t0/1/2:1,2,3,4,5,6,7,8,9,10\n"
        )
        result = ingest_vcf(path)
        self.assertEqual(len(result.accepted_records), 1)
        self.assertEqual(len(result.accepted_records[0].samples[0].pl), 10)

