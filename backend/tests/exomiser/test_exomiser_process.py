"""Synthetic Exomiser argument and process-adapter tests."""

import unittest
from unittest import mock

from app.exomiser.config import ExomiserInvocationConfig
from app.exomiser.exceptions import ExomiserExecutionError
from app.exomiser.process import (
    ExomiserProcessAdapter,
    build_exomiser_arguments,
)


class ExomiserProcessTests(unittest.TestCase):
    def setUp(self):
        self.config = ExomiserInvocationConfig(
            exomiser_version="15.1.0",
            exomiser_distribution_sha256="b" * 64,
            java_executable="java",
            jar_path="C:\\synthetic\\exomiser.jar",
            application_properties="C:\\synthetic\\application.properties",
            data_directory="C:\\synthetic\\data",
            hg19_data_version="2512",
            hg38_data_version=None,
            phenotype_data_version="2512",
            reference_data_identity="synthetic-data",
            reference_data_sha256="c" * 64,
            assembly="hg19",
            timeout_seconds=60,
            max_output_bytes=1024,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )

    def test_argument_array_uses_verified_analyse_contract(self):
        arguments = build_exomiser_arguments(
            self.config,
            analysis_path="C:\\synthetic\\analysis.yml",
            output_options_path="C:\\synthetic\\output.yml",
        )
        self.assertEqual(arguments[0], "java")
        self.assertEqual(arguments[1:4], ("-jar", "C:\\synthetic\\exomiser.jar", "analyse"))
        self.assertIn("--spring.config.location=C:\\synthetic\\application.properties", arguments)

    def test_nonzero_process_result_is_typed(self):
        adapter = ExomiserProcessAdapter(self.config)
        completed = mock.Mock(returncode=2, stdout="failed", stderr="synthetic stderr")
        with mock.patch(
            "app.exomiser.process.subprocess.run",
            return_value=completed,
        ):
            with self.assertRaises(ExomiserExecutionError) as raised:
                adapter.run(analysis_path="synthetic-analysis.yml")
        self.assertEqual(raised.exception.exit_code, 2)


if __name__ == "__main__":
    unittest.main()
