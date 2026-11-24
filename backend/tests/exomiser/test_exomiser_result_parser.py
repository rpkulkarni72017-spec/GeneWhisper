"""Synthetic Exomiser TSV parser tests."""

import unittest

from app.exomiser.result_parser import (
    parse_exomiser_tsv_gene,
    parse_exomiser_tsv_variant,
)


class ExomiserResultParserTests(unittest.TestCase):
    def test_gene_parser_preserves_native_rank_and_unknown_fields(self):
        text = (
            "RANK\tGENE_SYMBOL\tENTREZ_GENE_ID\tMOI\t"
            "EXOMISER_GENE_COMBINED_SCORE\tEXOMISER_GENE_PHENO_SCORE\t"
            "EXOMISER_GENE_VARIANT_SCORE\tUNVERIFIED_FIELD\n"
            "3\tSYNT1\t1001\tAD\t0.750\t0.600\t0.900\tsynthetic\n"
        )
        genes = parse_exomiser_tsv_gene(text)
        self.assertEqual(genes[0].rank, 3)
        self.assertEqual(genes[0].gene_symbol, "SYNT1")
        self.assertEqual(genes[0].combined_score, 0.75)
        self.assertEqual(
            dict(genes[0].unknown_fields),
            {"UNVERIFIED_FIELD": "synthetic"},
        )

    def test_variant_parser_preserves_raw_fields(self):
        text = (
            "RANK\tGENE_SYMBOL\tENTREZ_GENE_ID\tMOI\t"
            "EXOMISER_GENE_COMBINED_SCORE\tEXOMISER_GENE_PHENO_SCORE\t"
            "EXOMISER_GENE_VARIANT_SCORE\tEXOMISER_VARIANT_SCORE\t"
            "CONTRIBUTING_VARIANT\tWHITELIST_VARIANT\tVCF_ID\tREF\tALT\tFILTER\n"
            "3\tSYNT1\t1001\tAD\t0.750\t0.600\t0.900\t0.850\ttrue\tfalse\t"
            "synthetic-vcf-id\tA\tC\tPASS\n"
        )
        variants = parse_exomiser_tsv_variant(text)
        self.assertEqual(variants[0].vcf_id, "synthetic-vcf-id")
        self.assertEqual(variants[0].ref, "A")
        self.assertEqual(variants[0].alt, "C")
        self.assertEqual(variants[0].variant_score, 0.85)

    def test_missing_required_variant_header_is_rejected(self):
        with self.assertRaises(Exception):
            parse_exomiser_tsv_variant("RANK\tGENE_SYMBOL\n1\tSYNT1\n")


if __name__ == "__main__":
    unittest.main()
