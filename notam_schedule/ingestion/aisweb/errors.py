"""Errors raised by the AISWEB source."""
from ...domain.ports import SourceError


class AiswebError(SourceError):
    """The AISWEB API (or an offline file standing in for it) could not provide the data."""
