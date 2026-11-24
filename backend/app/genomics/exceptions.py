"""Validation exception categories for VCF ingestion."""


class VcfSourceError(ValueError):
    """The source cannot be read or is not an uncompressed VCF."""


class UnsupportedVcfFormatError(VcfSourceError):
    """Compression or an unsupported VCF version was encountered."""


class MalformedVcfHeaderError(VcfSourceError):
    """The VCF header is missing required declarations or is malformed."""


class AssemblyDeclarationError(VcfSourceError):
    """Assembly declarations conflict or cannot be used reliably."""


class MalformedRecordError(ValueError):
    """A complete record has malformed required evidence."""


class InvalidPositionError(MalformedRecordError):
    """A POS or contig-boundary value is invalid."""


class InvalidAlleleError(MalformedRecordError):
    """A REF, ALT or allele-index value is invalid."""


class UnsupportedAlleleError(MalformedRecordError):
    """A symbolic or breakend allele is outside the supported input boundary."""


class InvalidGenotypeError(MalformedRecordError):
    """A GT value is syntactically or semantically invalid."""


class InvalidFormatFieldError(MalformedRecordError):
    """A declared FORMAT field or record value is malformed."""


class DuplicateRecordError(MalformedRecordError):
    """A duplicate record was detected; duplicate records remain evidence."""

