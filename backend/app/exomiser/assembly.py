"""No-inference assembly compatibility boundary."""

from enum import Enum


class AssemblyCompatibility(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNRESOLVED = "unresolved"


class UnresolvedAssemblyMapper:
    """Default mapper: never invents assembly equivalence."""

    def compatible(self, *, phenopacket_label, exomiser_label):
        return AssemblyCompatibility.UNRESOLVED


class NoAutomaticAssemblyMapper(UnresolvedAssemblyMapper):
    """Explicit name for the Stage 6C no-mapping implementation."""
