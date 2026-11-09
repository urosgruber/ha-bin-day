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


class BinDayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bin Day."""

    VERSION = 1

    def __init__(self) -> None:
        self._matches: dict[str, SimbioAddress] = {}
        self._address: SimbioAddress | None = None
        self._fetched: dict[str, bool] = {}  # type key -> has a date now
        self._types: dict[str, dict[str, Any]] = {}

    @property
    def _sl(self) -> bool:
        return self.hass.config.language.lower().startswith("sl")

    def _default_name(self, key: str) -> str:
        known = TYPE_DEFAULTS.get(key)
        if known is None:
            return key
        return known.name_sl if self._sl else known.name_en

    def _default_icon(self, key: str) -> str:
        known = TYPE_DEFAULTS.get(key)
        return known.icon if known else _UNKNOWN_ICON

    def _default_color(self, key: str, index: int) -> str:
        known = TYPE_DEFAULTS.get(key)
        return known.color if known else default_color(index)

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
            data_schema=vol.Schema({vol.Required("address"): str}),
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
        """Choose the waste types and set the name, icon and colour of each."""
        errors: dict[str, str] = {}
        keys = list(self._fetched)
        if user_input is not None:
            selected = [k for k in keys if k in user_input["types"]]
            if not selected:
                errors["base"] = "no_types"
            elif any(
                not _COLOR_RE.match(user_input.get(f"color_{k}", "")) for k in selected
            ):
                errors["base"] = "invalid_color"
            else:
                self._types = {
                    k: {
                        "name": user_input[f"name_{k}"],
                        "icon": user_input[f"icon_{k}"],
                        "color": user_input[f"color_{k}"],
                        "estimate": True,
                        "interval": None,
                    }
                    for k in selected
                }
                return await self.async_step_name()

        suffix = _NO_COLLECTION["sl" if self._sl else "en"]
        options = [
            SelectOptionDict(
                value=k,
                label=f"{self._default_name(k)} {suffix}"
                if not self._fetched[k]
                else self._default_name(k),
            )
            for k in keys
        ]
        fields: dict[Any, Any] = {
            vol.Required("types", default=keys): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
        for index, key in enumerate(keys):
            fields[vol.Required(f"name_{key}", default=self._default_name(key))] = str
            fields[
                vol.Required(f"icon_{key}", default=self._default_icon(key))
            ] = IconSelector()
            fields[
                vol.Required(
                    f"color_{key}", default=self._default_color(key, index)
                )
            ] = TextSelector()
        return self.async_show_form(
            step_id="types", data_schema=vol.Schema(fields), errors=errors
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
