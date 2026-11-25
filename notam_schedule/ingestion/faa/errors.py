from ...domain.ports import SourceError


class FaaError(SourceError):
    """The FAA NMS-API could not provide NOTAMs (credentials, HTTP error, unexpected response)."""
