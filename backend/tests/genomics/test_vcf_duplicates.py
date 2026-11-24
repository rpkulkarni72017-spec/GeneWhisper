"""Synthetic whole-file duplicate traceability tests."""

import tempfile
import unittest
from pathlib import Path

from app.genomics.vcf_validation import ingest_vcf


PROJECT_ROOT = Path(r"D:\engineering\major\GeneWhisper")


class VcfDuplicateTests(unittest.TestCase):
    def test_duplicate_key_preserves_both_records_and_locations(self):
        with tempfile.TemporaryDirectory(
            prefix="stage5b-duplicates-",
            dir=str(PROJECT_ROOT),
        ) as temporary_directory:
            path = Path(temporary_directory) / "synthetic.vcf"
            path.write_text(
                "##fileformat=VCFv4.2\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
                "1\t10\tdup-a\tA\tC\t30\tPASS\tA=1\n"
                "1\t10\tdup-b\tA\tC\t40\tPASS\tA=2\n",
                encoding="utf-8",
                newline="",
            )

            result = ingest_vcf(path)

        self.assertEqual(len(result.accepted_records), 2)
        self.assertEqual(result.accepted_records[0].id_values, ("dup-a",))
        self.assertEqual(result.accepted_records[1].id_values, ("dup-b",))
        duplicate_issues = [
            issue
            for issue in result.issues
            if issue.category.value == "duplicate_record"
        ]
        self.assertEqual(len(duplicate_issues), 1)
        duplicate = duplicate_issues[0].duplicate
        self.assertEqual(duplicate.line_numbers, (3, 4))
        self.assertEqual(len(duplicate.source_record_ids), 2)

