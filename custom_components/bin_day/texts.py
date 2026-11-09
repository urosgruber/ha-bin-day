"""Texts in English and Slovenian, and the language choice."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_LANGUAGE

NONE_STATE = "none"

NEXT_PREFIX: dict[str, dict[str, str]] = {
    "en": {"today": "Today", "tomorrow": "Tomorrow"},
    "sl": {"today": "Danes", "tomorrow": "Jutri"},
}


def lang_for(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the language of the entry.

    The language option "en" or "sl" wins. Otherwise ("auto" or not set) the
    Home Assistant language decides: "sl" when it is Slovenian, else "en".
    """
    chosen = entry.options.get(CONF_LANGUAGE)
    if chosen in ("en", "sl"):
        return chosen
    language = hass.config.language or ""
    return "sl" if language.lower().startswith("sl") else "en"


def next_state(lang: str, kind: str, names: list[str]) -> str:
    """Return the state of the next-collection sensor."""
    if kind not in ("today", "tomorrow") or not names:
        return NONE_STATE
    prefix = NEXT_PREFIX.get(lang, NEXT_PREFIX["en"])[kind]
    return f"{prefix}: {', '.join(names)}"


# Reminder messages. The notification title is the entry title.
REMINDER_PREFIX: dict[str, dict[str, str]] = {
    "en": {"today": "Today", "tomorrow": "Tomorrow"},
    "sl": {"today": "Danes odvoz", "tomorrow": "Jutri odvoz"},
}

UNTIL_SUFFIX: dict[str, str] = {
    "en": " (until {until})",
    "sl": " (do {until})",
}

ESTIMATE_LINE: dict[str, str] = {
    "en": "* estimate – no data from the source",
    "sl": "* ocena – vir nima podatka",
}

MISSING_MESSAGE: dict[str, str] = {
    "en": "Waste collection data is missing. Check the source.",
    "sl": "Podatki o odvozu smeti manjkajo. Preveri vir podatkov.",
}


def _lang(lang: str) -> str:
    return lang if lang in REMINDER_PREFIX else "en"


def reminder_text(lang: str, kind: str, names: list[str], until: str | None) -> str:
    """Return a reminder: the due names, the time limit and the estimate note."""
    lang = _lang(lang)
    text = f"{REMINDER_PREFIX[lang][kind]}: {', '.join(names)}"
    if kind == "today" and until:
        text += UNTIL_SUFFIX[lang].format(until=until)
    if any(name.endswith("*") for name in names):
        text += "\n" + ESTIMATE_LINE[lang]
    return text


def missing_text(lang: str) -> str:
    """Return the warning that the collection data is missing."""
    return MISSING_MESSAGE[_lang(lang)]
