"""Strict assembly labels and future compatibility mapping."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class AssemblyStatus(str, Enum):
    EXPLICIT_SINGLE = "explicit_single"
    EXPLICIT_CONFLICTING = "explicit_conflicting"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AssemblyDeclaration:
    raw_label: str
    source: str


class AssemblyCompatibility(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNRESOLVED = "unresolved"


class AssemblyCompatibilityMapper(Protocol):
    def compatible(
        self,
        *,
        phenopacket_label: str,
        vcf_label: str,
    ) -> AssemblyCompatibility:
        """Resolve compatibility only through explicit external mapping data."""
        ...
