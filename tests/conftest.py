"""Shared test fixtures."""

# Import the package of this repository first. The test plugin ships its own
# `custom_components` package and would otherwise win the import.
import custom_components  # noqa: F401
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration."""
    yield


# HA's own http component triggers aiohttp's NotAppKeyWarning. A mark has a
# higher precedence than "-W error" on the command line; the same filter in
# pyproject.toml would lose against it.
_IGNORE_NOT_APP_KEY = "ignore::aiohttp.web_exceptions.NotAppKeyWarning"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Ignore aiohttp's NotAppKeyWarning in every test."""
    for item in items:
        item.add_marker(pytest.mark.filterwarnings(_IGNORE_NOT_APP_KEY))
