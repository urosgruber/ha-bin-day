"""Config flow for Bin Day: address, pick, types, name."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    IconSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_TYPES,
    DOMAIN,
    TYPE_DEFAULTS,
    default_color,
)
from .simbio import SimbioAddress, SimbioClient
from .source import SourceError

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_UNKNOWN_ICON = "mdi:delete"
_NO_COLLECTION = {"en": "(no collection now)", "sl": "(trenutno ni odvoza)"}


def default_type_name(key: str, sl: bool) -> str:
    """Return the default display name of a type key."""
    known = TYPE_DEFAULTS.get(key)
    if known is None:
        return key
    return known.name_sl if sl else known.name_en


def default_type_values(key: str, index: int, sl: bool) -> dict[str, str]:
    """Return the default name, icon and colour of a type at this position."""
    known = TYPE_DEFAULTS.get(key)
    return {
        "name": default_type_name(key, sl),
        "icon": known.icon if known else _UNKNOWN_ICON,
        "color": known.color if known else default_color(index),
    }


def type_schema(defaults: dict[str, str]) -> vol.Schema:
    """Return the schema of the name, icon and colour of one type."""
    return vol.Schema(
        {
            vol.Required("name", default=defaults["name"]): str,
            vol.Required("icon", default=defaults["icon"]): IconSelector(),
            vol.Required("color", default=defaults["color"]): TextSelector(),
        }
    )


def validate_type(user_input: dict[str, Any]) -> dict[str, str]:
    """Return field errors for the input of one type; empty when valid."""
    errors: dict[str, str] = {}
    if not user_input["name"].strip():
        errors["name"] = "invalid_name"
    if not _COLOR_RE.match(user_input["color"]):
        errors["color"] = "invalid_color"
    return errors


def type_result(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return the stored settings of one type."""
    return {
        "name": user_input["name"].strip(),
        "icon": user_input["icon"],
        "color": user_input["color"],
        "estimate": True,
        "interval": None,
    }


class BinDayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bin Day."""

    VERSION = 1

    def __init__(self) -> None:
        self._matches: dict[str, SimbioAddress] = {}
        self._address: SimbioAddress | None = None
        self._fetched: dict[str, bool] = {}  # type key -> has a date now
        self._types: dict[str, dict[str, Any]] = {}
        self._selected: list[str] = []

    @property
    def _sl(self) -> bool:
        return self.hass.config.language.lower().startswith("sl")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the street and house number, then search Simbio."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = SimbioClient(async_get_clientsession(self.hass))
            try:
                matches = await client.async_search(user_input["address"])
            except SourceError:
                errors["base"] = "cannot_connect"
            else:
                if not matches:
                    errors["base"] = "no_match"
                else:
                    self._matches = {m.id: m for m in matches}
                    return await self.async_step_pick()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required("address"): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user confirm one of the matching addresses."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = self._matches[user_input[CONF_ADDRESS_ID]]
            await self.async_set_unique_id(address.id)
            self._abort_if_unique_id_configured()
            client = SimbioClient(async_get_clientsession(self.hass))
            try:
                fetched = await client.async_fetch_address(address)
            except SourceError:
                errors["base"] = "cannot_connect"
            else:
                self._address = address
                self._fetched = {key: bool(dates) for key, dates in fetched.items()}
                return await self.async_step_types()
        options = [
            SelectOptionDict(value=m.id, label=f"{m.name}, {m.city}")
            for m in self._matches.values()
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_types(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which waste types to track."""
        errors: dict[str, str] = {}
        keys = list(self._fetched)
        if user_input is not None:
            self._selected = [k for k in keys if k in user_input["types"]]
            if not self._selected:
                errors["base"] = "no_types"
            else:
                self._types = {}
                return await self.async_step_type()

        suffix = _NO_COLLECTION["sl" if self._sl else "en"]
        name = lambda k: default_type_name(k, self._sl)  # noqa: E731
        options = [
            SelectOptionDict(
                value=k,
                label=name(k) if self._fetched[k] else f"{name(k)} {suffix}",
            )
            for k in keys
        ]
        schema = vol.Schema(
            {
                vol.Required("types", default=keys): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="types",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the name, icon and colour of the next selected type."""
        keys = list(self._fetched)
        key = next(k for k in self._selected if k not in self._types)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_type(user_input)
            if not errors:
                self._types[key] = type_result(user_input)
                if len(self._types) == len(self._selected):
                    return await self.async_step_name()
                return await self.async_step_type()
        defaults = default_type_values(key, keys.index(key), self._sl)
        schema = type_schema(defaults)
        return self.async_show_form(
            step_id="type",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            description_placeholders={"type": defaults["name"]},
            errors=errors,
        )

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the device name and create the entry."""
        assert self._address is not None
        if user_input is not None:
            return self.async_create_entry(
                title=user_input["name"],
                data={
                    CONF_ADDRESS_ID: self._address.id,
                    CONF_ADDRESS_NAME: self._address.name,
                    CONF_CITY: self._address.city,
                    CONF_TYPES: self._types,
                },
            )
        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "name", default=f"Bin Day – {self._address.name}"
                    ): str
                }
            ),
        )
