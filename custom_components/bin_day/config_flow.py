"""Config flow for Bin Day: address, pick, then one final step."""

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
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
from homeassistant.util import slugify

from .const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_INITIAL_SWITCH_STATES,
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
    ordered_keys,
)
from .reminders import DAY_BEFORE, EVENING, MORNING, REMINDERS
from .simbio import SimbioAddress, SimbioClient
from .source import SourceError

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_UNKNOWN_ICON = "mdi:delete"
# The address search needs this many characters; the pick list shows at most
# this many matches.
MIN_SEARCH_LENGTH = 3
MAX_MATCHES = 50
_ON = {key: f"{key}_on" for key in REMINDERS}
# The last choice of the type picker in the options.
_DONE = "done"
_DONE_LABEL = {"en": "Save and close", "sl": "Shrani in zapri"}
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
_PHONE_SYSTEMS = {"ios", "ipados", "android"}


def _offered_targets(hass: HomeAssistant) -> list[str]:
    """Return the names of the notify actions that a user can choose."""
    services = hass.services.async_services_for_domain("notify")
    return sorted(name for name in services if name not in _NOT_TARGETS)


def notify_targets(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Return the notify actions as options of a select."""
    return [SelectOptionDict(value=n, label=n) for n in _offered_targets(hass)]


def phone_targets(hass: HomeAssistant) -> list[str]:
    """Return the notify actions of the phones of the Companion app.

    A phone counts when its system is iOS, iPadOS or Android and its notify
    action exists now.
    """
    names: set[str] = set()
    for entry in hass.config_entries.async_entries("mobile_app"):
        system = str(entry.data.get("os_name", "")).lower()
        device = entry.data.get("device_name")
        if system not in _PHONE_SYSTEMS or not device:
            continue
        name = f"mobile_app_{slugify(device)}"
        if hass.services.has_service("notify", name):
            names.add(name)
    return sorted(names)


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
                        if len(self._matches) > 1:
                            return await self.async_step_pick()
                        # One match: continue as if the user picked it.
                        address = next(iter(self._matches.values()))
                        if await self._async_choose(address):
                            return await self.async_step_name()
                        errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required("address"): str}), user_input
            ),
            errors=errors,
            last_step=False,
        )

    async def _async_choose(self, address: SimbioAddress) -> bool:
        """Take this address and fetch its types.

        Abort the flow when the address is set up. Return False when Simbio
        cannot be reached.
        """
        await self.async_set_unique_id(address.id)
        self._abort_if_unique_id_configured()
        client = SimbioClient(async_get_clientsession(self.hass))
        try:
            fetched = await client.async_fetch_address(address)
        except SourceError:
            return False
        self._address = address
        self._fetched = {key: bool(dates) for key, dates in fetched.items()}
        return True

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user confirm one of the matching addresses."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = self._matches[user_input[CONF_ADDRESS_ID]]
            if await self._async_choose(address):
                return await self.async_step_name()
            errors["base"] = "cannot_connect"
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
            last_step=False,
        )

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the types, the name and the phones, then create the entry.

        Every selected type gets its default name, icon and colour. The user
        can change them later in the options.
        """
        assert self._address is not None
        errors: dict[str, str] = {}
        fetched_keys = list(self._fetched)
        keys = ordered_keys({k: {} for k in fetched_keys})
        if user_input is not None:
            selected = [k for k in keys if k in user_input["types"]]
            if not selected:
                errors["base"] = "no_types"
            else:
                targets = list(user_input.get(CONF_NOTIFY_TARGETS, []))
                types = {
                    key: type_result(
                        default_type_values(key, fetched_keys.index(key), self._sl)
                    )
                    for key in selected
                }
                # No phones chosen: reminders stay off, as the code default.
                options: dict[str, Any] = {}
                if targets:
                    options = {
                        CONF_REMINDERS_ENABLED: True,
                        CONF_NOTIFY_TARGETS: targets,
                    }
                return self.async_create_entry(
                    title=user_input["name"],
                    data={
                        CONF_ADDRESS_ID: self._address.id,
                        CONF_ADDRESS_NAME: self._address.name,
                        CONF_CITY: self._address.city,
                        CONF_TYPES: types,
                    },
                    options=options,
                )

        suffix = _NO_COLLECTION["sl" if self._sl else "en"]
        name = lambda k: default_type_name(k, self._sl)  # noqa: E731
        type_options = [
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
                        options=type_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        sort=False,
                    )
                ),
                vol.Required(
                    "name", default=f"Bin Day – {self._address.name}"
                ): str,
                vol.Optional(
                    CONF_NOTIFY_TARGETS, default=phone_targets(self.hass)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_targets(self.hass),
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="name",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class BinDayOptionsFlow(OptionsFlow):
    """Edit the waste types, the reminders and the general settings.

    The reminders and general steps save at once and end the flow. The types
    steps collect changes on the flow and save them when the user chooses
    "done". Closing the dialog saves nothing. The entry reloads after a save.
    """

    def __init__(self) -> None:
        self._key: str | None = None
        # Type overrides edited in this visit. None until the first edit.
        self._pending: dict[str, dict[str, Any]] | None = None

    def _pending_types(self) -> dict[str, dict[str, Any]]:
        """Return the type overrides, with the edits of this visit."""
        if self._pending is None:
            self._pending = {
                key: dict(conf)
                for key, conf in (self.config_entry.options.get(CONF_TYPES) or {}).items()
            }
        return self._pending

    def _current_types(self) -> dict[str, dict[str, Any]]:
        """Return every type with the edits of this visit applied."""
        pending = self._pending_types()
        return {
            key: {**conf, **(pending.get(key) or {})}
            for key, conf in (self.config_entry.data.get(CONF_TYPES) or {}).items()
        }

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
        """Choose which waste type to edit, or save and close."""
        types = self._current_types()
        if user_input is not None:
            if user_input["type"] == _DONE:
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_TYPES: self._pending_types()}
                )
            self._key = user_input["type"]
            return await self.async_step_type()
        keys = ordered_keys(types)
        options = [SelectOptionDict(value=key, label=types[key]["name"]) for key in keys]
        options.append(
            SelectOptionDict(value=_DONE, label=_DONE_LABEL["sl" if self._sl else "en"])
        )
        return self.async_show_form(
            step_id="types",
            data_schema=vol.Schema(
                {
                    vol.Required("type", default=keys[0]): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            sort=False,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def async_step_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the name, icon, colour, estimate and interval of one type."""
        assert self._key is not None
        current = self._current_types()[self._key]
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
                self._pending_types()[self._key] = result
                return await self.async_step_types()
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
            last_step=False,
        )

    # ----- reminders -----

    def _switch_entity_id(self, key: str) -> str | None:
        """Return the entity id of a reminder switch, or None when it is missing."""
        return er.async_get(self.hass).async_get_entity_id(
            "switch", DOMAIN, f"{self.config_entry.entry_id}_reminder_{key}"
        )

    def _switch_is_on(self, key: str) -> bool:
        """Return the state of a reminder switch, or its default when missing."""
        entity_id = self._switch_entity_id(key)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is not None and state.state in (STATE_ON, STATE_OFF):
            return state.state == STATE_ON
        wanted = self.config_entry.options.get(CONF_INITIAL_SWITCH_STATES) or {}
        return bool(wanted.get(key, REMINDERS[key][2]))

    async def _apply_switches(self, user_input: dict[str, Any]) -> dict[str, bool]:
        """Set the existing reminder switches to the wanted states.

        Return the wanted states of the switches that do not exist yet.
        """
        missing: dict[str, bool] = {}
        for key in REMINDERS:
            wanted = bool(user_input[_ON[key]])
            entity_id = self._switch_entity_id(key)
            if entity_id is None:
                missing[key] = wanted
                continue
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == (STATE_ON if wanted else STATE_OFF):
                continue
            await self.hass.services.async_call(
                "switch",
                "turn_on" if wanted else "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
        return missing

    async def async_step_reminders(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the reminders, their targets and times."""
        options = self.config_entry.options
        offered = _offered_targets(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            enabled = bool(user_input[CONF_REMINDERS_ENABLED])
            # Reminders on with no targets is valid: it means events only.
            chosen = list(user_input.get(CONF_NOTIFY_TARGETS, []))
        if user_input is not None and not errors:
            initial = await self._apply_switches(user_input)
            if not enabled:
                # Turning reminders off removes the switches. Keep every
                # wanted state, so the switches come back as the user set them.
                initial = {key: bool(user_input[_ON[key]]) for key in REMINDERS}
            changes: dict[str, Any] = {
                CONF_REMINDERS_ENABLED: enabled,
                CONF_NOTIFY_TARGETS: chosen,
                CONF_EVENING_TIME: _hhmm(user_input[CONF_EVENING_TIME]),
                CONF_MORNING_TIME: _hhmm(user_input[CONF_MORNING_TIME]),
                CONF_DAY_BEFORE_TIME: _hhmm(user_input[CONF_DAY_BEFORE_TIME]),
            }
            new = {**options, **changes}
            # Wanted states of switches that do not exist yet. Saved targets
            # that are no longer offered are dropped above.
            if initial:
                new[CONF_INITIAL_SWITCH_STATES] = initial
            else:
                new.pop(CONF_INITIAL_SWITCH_STATES, None)
            return self.async_create_entry(data=new)
        saved = options.get(CONF_NOTIFY_TARGETS) or []
        if saved or options.get(CONF_REMINDERS_ENABLED, False):
            # Reminders on with no saved target is "events only": keep it.
            targets = [t for t in saved if t in offered]
        else:
            # Reminders off and no saved target: offer the phones.
            targets = phone_targets(self.hass)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REMINDERS_ENABLED,
                    default=options.get(CONF_REMINDERS_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(CONF_NOTIFY_TARGETS, default=targets): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_targets(self.hass),
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    _ON[EVENING], default=self._switch_is_on(EVENING)
                ): BooleanSelector(),
                vol.Required(
                    CONF_EVENING_TIME,
                    default=options.get(CONF_EVENING_TIME, DEFAULT_EVENING_TIME),
                ): TimeSelector(),
                vol.Required(
                    _ON[MORNING], default=self._switch_is_on(MORNING)
                ): BooleanSelector(),
                vol.Required(
                    CONF_MORNING_TIME,
                    default=options.get(CONF_MORNING_TIME, DEFAULT_MORNING_TIME),
                ): TimeSelector(),
                vol.Required(
                    _ON[DAY_BEFORE], default=self._switch_is_on(DAY_BEFORE)
                ): BooleanSelector(),
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
