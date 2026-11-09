"""Shared test fixtures."""

# Import the package of this repository first. The test plugin ships its own
# `custom_components` package and would otherwise win the import.
import custom_components  # noqa: F401
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration."""
    yield
