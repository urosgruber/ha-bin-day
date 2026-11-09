"""Config flow for Bin Day (placeholder until the real flow is built)."""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class BinDayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bin Day."""

    VERSION = 1
