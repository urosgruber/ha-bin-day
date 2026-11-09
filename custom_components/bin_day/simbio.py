"""Client for the Simbio "my waste collection day" web service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT, SIMBIO_ACTION, SIMBIO_URL, USER_AGENT
from .source import AddressNotFound, SourceError

_NEXT_PREFIX = "next_"


@dataclass(frozen=True)
class SimbioAddress:
    """An address known to Simbio."""

    id: str
    name: str
    city: str


def parse_date(value: str | None) -> date | None:
    """Parse "četrtek, 12. 11. 2026". Return None when there is no date."""
    if not value or "," not in value:
        return None
    text = re.sub(r"\s+", " ", value.split(",", 1)[1].strip())
    try:
        return datetime.strptime(text, "%d. %m. %Y").date()
    except ValueError:
        return None


class SimbioClient:
    """Query the Simbio service."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _query(self, query: str) -> list[dict[str, Any]]:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.post(
                    SIMBIO_URL,
                    data={"action": SIMBIO_ACTION, "query": query},
                    headers={"User-Agent": USER_AGENT},
                )
                async with resp:
                    if resp.status != 200:
                        raise SourceError(f"Simbio answered HTTP {resp.status}")
                    text = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SourceError(f"Simbio request failed: {err}") from err
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise SourceError("Simbio answer is not JSON") from err
        if not isinstance(payload, list):
            raise SourceError("Simbio answer is not a list")
        if not all(isinstance(item, dict) for item in payload):
            raise SourceError("Simbio answer has an unexpected item")
        return payload

    async def async_search(self, query: str) -> list[SimbioAddress]:
        """Return all addresses that match the query."""
        return [
            SimbioAddress(
                str(i["id"]),
                str(i.get("name", "")).strip(),
                str(i.get("city", "")).strip(),
            )
            for i in await self._query(query)
            if "id" in i
        ]

    async def async_fetch_address(
        self, address: SimbioAddress
    ) -> dict[str, list[date]]:
        """Return type key -> dates for one address."""
        for item in await self._query(f"{address.name} {address.city}"):
            if str(item.get("id")) == address.id:
                result: dict[str, list[date]] = {}
                for key, value in item.items():
                    if key.startswith(_NEXT_PREFIX) and len(key) > len(_NEXT_PREFIX):
                        parsed = parse_date(value if isinstance(value, str) else None)
                        result[key[len(_NEXT_PREFIX):]] = [parsed] if parsed else []
                if not result:
                    raise SourceError("Simbio item has no collection types")
                return result
        raise AddressNotFound(address.id)


class SimbioSource:
    """Source for one Simbio address."""

    def __init__(self, client: SimbioClient, address: SimbioAddress) -> None:
        self._client = client
        self._address = address

    async def async_fetch(self) -> dict[str, list[date]]:
        return await self._client.async_fetch_address(self._address)
