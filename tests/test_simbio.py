"""Tests for the Simbio client."""

from datetime import date
from pathlib import Path

import pytest

from custom_components.bin_day.const import SIMBIO_URL, USER_AGENT
from custom_components.bin_day.simbio import (
    SimbioAddress,
    SimbioClient,
    SimbioSource,
    parse_date,
)
from custom_components.bin_day.source import AddressNotFound, SourceError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

FIXTURES = Path(__file__).parent / "fixtures"
ID_1A = "100400000112129998"
ID_18A = "100400000112130001"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
async def client(hass, aioclient_mock):
    return SimbioClient(async_get_clientsession(hass))


def test_parse_date():
    assert parse_date("četrtek, 12. 11. 2026") == date(2026, 11, 12)
    assert parse_date(" petek,  13. 11. 2026 ") == date(2026, 11, 13)
    assert parse_date("ni odvoza") is None
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("x, 31. 2. 2026") is None


async def test_search_returns_all_matches(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_two.json"))
    result = await client.async_search("ljubljanska cesta celje")
    assert [a.name for a in result] == [
        "LJUBLJANSKA CESTA 1 A",
        "LJUBLJANSKA CESTA 18 A",
    ]
    assert {a.city for a in result} == {"CELJE"}
    method, url, data, headers = aioclient_mock.mock_calls[0]
    assert data["action"] == "simbioOdvozOdpadkov"
    assert data["query"] == "ljubljanska cesta celje"
    assert headers["User-Agent"] == USER_AGENT


async def test_search_empty(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_empty.json"))
    assert await client.async_search("nowhere") == []


async def test_fetch_picks_by_id(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_two.json"))
    address = SimbioAddress(ID_18A, "LJUBLJANSKA CESTA 18 A", "CELJE")
    result = await client.async_fetch_address(address)
    assert result == {
        "mko": [date(2026, 11, 18)],
        "emb": [date(2026, 11, 19)],
        "bio": [date(2026, 11, 20)],
    }
    assert aioclient_mock.mock_calls[0][2]["query"] == "LJUBLJANSKA CESTA 18 A CELJE"


async def test_fetch_no_date_type_is_empty_list(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_one.json"))
    address = SimbioAddress(ID_1A, "LJUBLJANSKA CESTA 1 A", "CELJE")
    result = await client.async_fetch_address(address)
    assert result["bio"] == []
    assert result["mko"] == [date(2026, 11, 12)]


async def test_fetch_unknown_key(client, aioclient_mock):
    body = (
        '[{"id": "1", "name": "A", "city": "B",'
        ' "next_tekstil": "sreda, 18. 11. 2026"}]'
    )
    aioclient_mock.post(SIMBIO_URL, text=body)
    result = await client.async_fetch_address(SimbioAddress("1", "A", "B"))
    assert result["tekstil"] == [date(2026, 11, 18)]


async def test_fetch_address_gone(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_empty.json"))
    with pytest.raises(AddressNotFound):
        await client.async_fetch_address(SimbioAddress(ID_1A, "X", "Y"))


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"status": 500, "text": "boom"},
        {"text": "null"},
        {"text": '{"a": 1}'},
        {"text": "<html>error</html>"},
    ],
)
async def test_fetch_errors(client, aioclient_mock, kwargs):
    aioclient_mock.post(SIMBIO_URL, **kwargs)
    with pytest.raises(SourceError):
        await client.async_fetch_address(SimbioAddress(ID_1A, "X", "Y"))
    with pytest.raises(SourceError):
        await client.async_search("x")


async def test_fetch_timeout(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, exc=TimeoutError)
    with pytest.raises(SourceError):
        await client.async_search("x")


async def test_source_delegates(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text=fixture("simbio_one.json"))
    source = SimbioSource(client, SimbioAddress(ID_1A, "LJUBLJANSKA CESTA 1 A", "CELJE"))
    assert (await source.async_fetch())["emb"] == [date(2026, 11, 13)]


async def test_fetch_item_without_types_is_error(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text='[{"id": "1", "name": "A", "city": "B"}]')
    with pytest.raises(SourceError) as err:
        await client.async_fetch_address(SimbioAddress("1", "A", "B"))
    assert not isinstance(err.value, AddressNotFound)


async def test_non_dict_item_is_source_error(client, aioclient_mock):
    aioclient_mock.post(SIMBIO_URL, text='["x"]')
    with pytest.raises(SourceError) as err:
        await client.async_fetch_address(SimbioAddress("1", "A", "B"))
    assert not isinstance(err.value, AddressNotFound)
    with pytest.raises(SourceError):
        await client.async_search("x")
