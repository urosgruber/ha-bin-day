"""Texts in English and Slovenian, and the language choice."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

NONE_STATE = "none"

NEXT_PREFIX: dict[str, dict[str, str]] = {
    "en": {"today": "Today", "tomorrow": "Tomorrow"},
    "sl": {"today": "Danes", "tomorrow": "Jutri"},
}


def lang_for(hass: HomeAssistant) -> str:
    """Return "sl" when the HA language is Slovenian, else "en"."""
    language = hass.config.language or ""
    return "sl" if language.lower().startswith("sl") else "en"


def next_state(lang: str, kind: str, names: list[str]) -> str:
    """Return the state of the next-collection sensor."""
    if kind not in ("today", "tomorrow") or not names:
        return NONE_STATE
    prefix = NEXT_PREFIX.get(lang, NEXT_PREFIX["en"])[kind]
    return f"{prefix}: {', '.join(names)}"
