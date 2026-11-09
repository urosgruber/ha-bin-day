"""Config flow for Bin Day: address, pick, types, name."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_LANGUAGE,
    CONF_MISSING_WARNING,
    CONF_MORNING_TIME,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
    DOMAIN,
    INTERVAL_MAX,
    LANGUAGE_AUTO,
    LANGUAGES,
    INTERVAL_MIN,
    TYPE_DEFAULTS,
    default_color,
)
from .coordinator import (
    DEFAULT_DAY_BEFORE_TIME,
    DEFAULT_EVENING_TIME,
    DEFAULT_MORNING_TIME,
    get_types,
)
from .simbio import SimbioAddress, SimbioClient
from .source import SourceError

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_UNKNOWN_ICON = "mdi:delete"
# The address search needs this many characters; the pick list shows at most
# this many matches.
MIN_SEARCH_LENGTH = 3
MAX_MATCHES = 50
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


def _hhmm(value: str) -> str:
    """Normalise a time selector value (HH:MM or HH:MM:SS) to HH:MM."""
    return value[:5]


# Notify actions that are not a person's device.
_NOT_TARGETS = {"send_message", "persistent_notification"}


class BinDayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bin Day."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return BinDayOptionsFlow()

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
            text = user_input["address"].strip()
            if len(text) < MIN_SEARCH_LENGTH:
                errors["address"] = "too_short"
            else:
                client = SimbioClient(async_get_clientsession(self.hass))
                try:
                    matches = await client.async_search(text)
                except SourceError:
                    errors["base"] = "cannot_connect"
                else:
                    if not matches:
                        errors["base"] = "no_match"
                    else:
                        self._matches = {m.id: m for m in matches[:MAX_MATCHES]}
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


class BinDayOptionsFlow(OptionsFlow):
    """Edit the waste types, the reminders and the general settings.

    Each sub-step saves at once and ends the flow. The entry reloads.
    """

    def __init__(self) -> None:
        self._key: str | None = None

    @property
    def _sl(self) -> bool:
        return self.hass.config.language.lower().startswith("sl")

    def _save(self, **changes: Any) -> ConfigFlowResult:
        options = {**self.config_entry.options, **changes}
        return self.async_create_entry(data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["types", "reminders", "general"]
        )

    # ----- types -----

    async def async_step_types(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which waste type to edit."""
        types = get_types(self.config_entry)
        if user_input is not None:
            self._key = user_input["type"]
            return await self.async_step_type()
        options = [
            SelectOptionDict(value=key, label=conf["name"]) for key, conf in types.items()
        ]
        return self.async_show_form(
            step_id="types",
            data_schema=vol.Schema(
                {
                    vol.Required("type"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the name, icon, colour, estimate and interval of one type."""
        assert self._key is not None
        current = get_types(self.config_entry)[self._key]
        errors: dict[str, str] = {}
        interval: int | None = None
        if user_input is not None:
            errors = validate_type(user_input)
            raw = user_input.get("interval")
            if raw is not None:
                interval = int(raw) if float(raw).is_integer() else -1
                if not INTERVAL_MIN <= interval <= INTERVAL_MAX:
                    errors["interval"] = "invalid_interval"
            if not errors:
                result = type_result(user_input)
                result["estimate"] = bool(user_input["estimate"])
                result["interval"] = interval
                saved = dict(self.config_entry.options.get(CONF_TYPES) or {})
                saved[self._key] = result
                return self._save(**{CONF_TYPES: saved})
        schema = type_schema(
            {k: current[k] for k in ("name", "icon", "color")}
        ).extend(
            {
                vol.Required("estimate", default=current.get("estimate", True)): BooleanSelector(),
                vol.Optional("interval"): NumberSelector(
                    NumberSelectorConfig(step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        suggested = user_input
        if suggested is None and current.get("interval") is not None:
            suggested = {"interval": current["interval"]}
        return self.async_show_form(
            step_id="type",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            description_placeholders={"type": current["name"]},
            errors=errors,
        )

    # ----- reminders -----

    async def async_step_reminders(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the reminders, their targets and times."""
        options = self.config_entry.options
        services = self.hass.services.async_services_for_domain("notify")
        offered = sorted(name for name in services if name not in _NOT_TARGETS)
        errors: dict[str, str] = {}
        if user_input is not None:
            enabled = bool(user_input[CONF_REMINDERS_ENABLED])
            chosen = list(user_input.get(CONF_NOTIFY_TARGETS, []))
            if enabled and not chosen:
                errors[CONF_NOTIFY_TARGETS] = "no_targets"
        if user_input is not None and not errors:
            # Saved targets that are no longer offered are dropped here.
            return self._save(
                **{
                    CONF_REMINDERS_ENABLED: enabled,
                    CONF_NOTIFY_TARGETS: chosen,
                    CONF_EVENING_TIME: _hhmm(user_input[CONF_EVENING_TIME]),
                    CONF_MORNING_TIME: _hhmm(user_input[CONF_MORNING_TIME]),
                    CONF_DAY_BEFORE_TIME: _hhmm(user_input[CONF_DAY_BEFORE_TIME]),
                }
            )
        targets = [t for t in options.get(CONF_NOTIFY_TARGETS, []) if t in offered]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REMINDERS_ENABLED,
                    default=options.get(CONF_REMINDERS_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(CONF_NOTIFY_TARGETS, default=targets): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=n, label=n) for n in offered],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_EVENING_TIME,
                    default=options.get(CONF_EVENING_TIME, DEFAULT_EVENING_TIME),
                ): TimeSelector(),
                vol.Required(
                    CONF_MORNING_TIME,
                    default=options.get(CONF_MORNING_TIME, DEFAULT_MORNING_TIME),
                ): TimeSelector(),
                vol.Required(
                    CONF_DAY_BEFORE_TIME,
                    default=options.get(CONF_DAY_BEFORE_TIME, DEFAULT_DAY_BEFORE_TIME),
                ): TimeSelector(),
            }
        )
        return self.async_show_form(
            step_id="reminders",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    # ----- general -----

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the missing-data warning, the collection time and the language."""
        options = self.config_entry.options
        if user_input is not None:
            changes: dict[str, Any] = {
                CONF_MISSING_WARNING: bool(user_input[CONF_MISSING_WARNING]),
                CONF_LANGUAGE: user_input.get(CONF_LANGUAGE, LANGUAGE_AUTO),
            }
            new = {**options, **changes}
            if user_input.get(CONF_COLLECTION_TIME):
                new[CONF_COLLECTION_TIME] = _hhmm(user_input[CONF_COLLECTION_TIME])
            else:
                new.pop(CONF_COLLECTION_TIME, None)
            return self.async_create_entry(data=new)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MISSING_WARNING, default=options.get(CONF_MISSING_WARNING, True)
                ): BooleanSelector(),
                vol.Optional(CONF_COLLECTION_TIME): TimeSelector(),
                vol.Required(
                    CONF_LANGUAGE, default=options.get(CONF_LANGUAGE, LANGUAGE_AUTO)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(LANGUAGES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_LANGUAGE,
                    )
                ),
            }
        )
        suggested = (
            {CONF_COLLECTION_TIME: options[CONF_COLLECTION_TIME]}
            if options.get(CONF_COLLECTION_TIME)
            else None
        )
        return self.async_show_form(
            step_id="general",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )
