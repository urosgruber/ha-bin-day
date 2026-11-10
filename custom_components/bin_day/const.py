"""Constants for the Bin Day integration."""

from dataclasses import dataclass
from datetime import time, timedelta

DOMAIN = "bin_day"

# Config entry data keys.
CONF_ADDRESS_ID = "address_id"
CONF_ADDRESS_NAME = "address_name"
CONF_CITY = "city"
CONF_TYPES = "types"  # type key -> {name, icon, color, estimate, interval}

# Option keys.
CONF_REMINDERS_ENABLED = "reminders_enabled"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_EVENING_TIME = "evening_time"
CONF_MORNING_TIME = "morning_time"
CONF_DAY_BEFORE_TIME = "day_before_time"
CONF_MISSING_WARNING = "missing_warning"
CONF_COLLECTION_TIME = "collection_time"
CONF_LANGUAGE = "language"
# Wanted start states of the reminder switches: reminder key -> on or off.
# Used only when a switch has no restored state.
CONF_INITIAL_SWITCH_STATES = "initial_switch_states"

# Values of the language option.
LANGUAGE_AUTO = "auto"
LANGUAGES = (LANGUAGE_AUTO, "en", "sl")

# Estimates.
DEFAULT_INTERVAL = 14
INTERVAL_MIN = 7
INTERVAL_MAX = 35
TRUST_INTERVALS = 2

# Fetching.
FETCH_TIMES = (time(6, 0), time(12, 0), time(18, 30))
RETRY_DELAY = timedelta(minutes=15)
RETRY_MAX = 4
PRE_REMINDER_FETCH = timedelta(minutes=30)

# A collection counts as done at this time when no collection time is set.
DEFAULT_DONE = time(12, 0)


@dataclass(frozen=True)
class TypeDefault:
    """Default name, icon and colour of a known waste type."""

    name_en: str
    name_sl: str
    icon: str
    color: str


TYPE_DEFAULTS: dict[str, TypeDefault] = {
    "mko": TypeDefault("Mixed waste", "Mešani komunalni odpadki", "mdi:trash-can", "#00a136"),
    "emb": TypeDefault("Packaging", "Embalaža", "mdi:recycle", "#ffdd00"),
    "bio": TypeDefault("Bio waste", "Biološki odpadki", "mdi:leaf", "#8d6e63"),
}

# Colours for unknown types, after the colour of the three known types.
EXTRA_COLORS = ("#1e88e5", "#757575")

_COLOR_CYCLE = ("#00a136", "#ffdd00", "#8d6e63", *EXTRA_COLORS)


def default_color(index: int) -> str:
    """Return the colour for the type at this position; the list repeats."""
    return _COLOR_CYCLE[index % len(_COLOR_CYCLE)]

# Simbio web service.
SIMBIO_URL = "https://www.simbio.si/sl/moj-dan-odvoza-odpadkov"
SIMBIO_ACTION = "simbioOdvozOdpadkov"
USER_AGENT = "BinDay/0.1.5 (+https://github.com/urosgruber/ha-bin-day)"
REQUEST_TIMEOUT = 20

# Events fired on the HA bus, so other automations can react.
EVENT_REMINDER = "bin_day_reminder"
EVENT_DATA_MISSING = "bin_day_data_missing"
