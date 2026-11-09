"""Source protocol and errors."""

from __future__ import annotations

from datetime import date
from typing import Protocol


class SourceError(Exception):
    """The source could not deliver data."""


class AddressNotFound(SourceError):
    """The stored address is no longer known to the source."""


class Source(Protocol):
    """A provider of collection dates."""

    async def async_fetch(self) -> dict[str, list[date]]:
        """Return type key -> sorted dates (may be empty for a known type)."""
