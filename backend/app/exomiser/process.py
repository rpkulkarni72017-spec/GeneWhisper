"""Argument-array Exomiser process adapter."""

import subprocess
from typing import Tuple

from .exceptions import (
    ExomiserExecutionError,
    ExomiserTimeoutError,
)


def build_exomiser_arguments(
    config,
    *,
    analysis_path,
    output_options_path=None,
) -> Tuple[str, ...]:
    """Build an argument-array invocation without shell-string execution."""

    if not analysis_path:
        raise ExomiserExecutionError("analysis path is required")
    arguments = [
        config.java_executable,
        "-jar",
        config.jar_path,
        "analyse",
        "--analysis",
        analysis_path,
    ]
    if output_options_path:
        arguments.extend(["--output", output_options_path])
    arguments.extend(
        ["--spring.config.location={}".format(config.application_properties)]
    )
    return tuple(arguments)


class ExomiserProcessAdapter:
    """Invoke Exomiser only when the caller explicitly requests execution."""

    def __init__(self, config):
        self._config = config

    def run(
        self,
        *,
        analysis_path,
        output_options_path=None,
        preflight_report=None,
    ):
        if preflight_report is not None and not preflight_report.can_execute:
            raise ExomiserExecutionError("Exomiser execution was blocked by preflight")

        arguments = build_exomiser_arguments(
            self._config,
            analysis_path=analysis_path,
            output_options_path=output_options_path,
        )
        try:
            completed = subprocess.run(
                list(arguments),
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ExomiserTimeoutError(
                "Exomiser timed out",
                stdout=error.stdout,
                stderr=error.stderr,
            ) from error
        except OSError as error:
            raise ExomiserExecutionError(
                "could not start Exomiser",
            ) from error

        stdout = completed.stdout
        stderr = completed.stderr
        if len(stdout.encode("utf-8")) > self._config.max_stdout_bytes:
            stdout = "<stdout truncated>"
        if len(stderr.encode("utf-8")) > self._config.max_stderr_bytes:
            stderr = "<stderr truncated>"
        if completed.returncode != 0:
            raise ExomiserExecutionError(
                "Exomiser exited with non-zero status",
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return {
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
