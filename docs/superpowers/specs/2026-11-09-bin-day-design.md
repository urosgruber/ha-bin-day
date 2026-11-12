# Bin Day: a HACS integration for waste collection reminders and a card

Date: 2026-11-09. Status: approved by the user ("all good"), after a
builder check. **Revised the same day:** the user asked for a built-in
Simbio source, with WCS as a later iteration (see "Data sources" and
"Iterations"). The revision needs the user's approval again.

Earlier work: `2026-11-09-waste-collection-design.md` (the hand-built version
in the user's own HA). Bin Day turns that version into a package that other
people can install.

## Intent

The user built a waste-collection card, estimated dates and phone reminders
for their own HA on top of Waste Collection Schedule (WCS). It works, but it
lives in hand-made helpers, Jinja macros, two automations and a button-card
template. Nobody else can install it.

Bin Day packages the same behaviour so that other people can install it
from HACS and set it up in the HA UI. Simbio users in Slovenia are the first
audience: for them Bin Day fetches the data itself, so they install one
thing and enter only their address. A later iteration adds WCS as a second
source, so that users of any other company can use Bin Day too.

The user will publish it under their GitHub account `urosgruber`.

## Decisions by the user

- **v0.1 fetches Simbio data itself** (built-in source). WCS is not needed.
- **WCS becomes an optional second source in the next iteration**, planned
  separately. v0.1 must be built so that this source fits in without
  changes to the estimates, entities, card or reminders.
- Only Simbio is built in. Other companies come through WCS later.
- Bin Day is one HACS integration. It provides everything in one install:
  the data, the card, the estimates, the badge sensor and the reminders.
- All setup happens in the HA UI. No YAML.
- Reminders are part of the package, but optional (off by default).
- Languages: English and Slovenian, for the setup screens, the card and the
  reminder texts.
- Name: "Bin Day", domain `bin_day`, repository `urosgruber/ha-bin-day`.
- The card design is the one the user approved for their own HA (see
  "Card"). A dependency on button-card would be acceptable to the user, but
  see the builder's calls.

## Goals

- A Simbio user gets a working card and correct dates within a few minutes
  of installing, by entering only their address, with no YAML.
- Reminders never fail silently: they read dates, not counters, and they use
  an estimate (marked as one) when the source has no date.
- The card makes the next collection impossible to miss on the day before
  and on the day itself, and stays quiet on other days.

## Non-goals

- No built-in source for any company other than Simbio.
- No WCS source in v0.1 (next iteration).
- No "bins are out" button or acknowledgement.
- No yearly calendar view and no calendar entity in v0.1. Simbio gives only
  the next date per type.

## Anti-goals (failure even if it technically works)

- A reminder for the wrong day, or no reminder because data was late.
- An estimated date shown as if it were a fact.
- A setup that needs YAML or hand-made helpers.

## Constraints

- HA 2026.11 or later (the user's version).
- Simbio is called politely: at most a few requests per address per day,
  with a clear User-Agent naming Bin Day (no personal data in it).
- One GitHub repository can be only one HACS category. The integration
  therefore ships the card file itself and registers it in the frontend.
  HA already does this for other integrations (the user's HA loads
  `/ha_washdata/ha-washdata-card.js` this way).
- Must pass `hassfest` and the HACS validation action, so that it can later
  go into the default HACS list.

## Data sources

All logic after the source (estimates, entities, card, reminders) works on
one shape: **per type key, a list of real future dates** (possibly empty).
A source only produces that shape. *Builder's call: this is the interface
the WCS source will implement in the next iteration.*

### Simbio (v0.1, built in)

Verified against the live site on 2026-11-09:

- Request: `POST https://www.simbio.si/sl/moj-dan-odvoza-odpadkov`, form data
  `action=simbioOdvozOdpadkov` and `query=<free text>`.
- Answer: a JSON list of **all addresses that match the text** (for example
  "Ljubljanska" gives 50, and "Ljubljanska cesta 1 A" also gives "… 18 A").
  An unknown address gives `[]`. Each item has `id` (fixed), `name` (e.g.
  "LJUBLJANSKA CESTA 1 A"), `city`, and `next_mko`, `next_emb`, `next_bio`.
- A date value is `"<weekday>, D. M. YYYY"` (e.g. "četrtek, 12. 11. 2026").
  "ni odvoza" (or any value without a comma) means no date.
- Types: `mko` = Mešani komunalni odpadki / Mixed waste, `emb` = Embalaža /
  Packaging, `bio` = Biološki odpadki / Bio waste. Any other `next_*` key
  becomes a type with its key as the name.
- Bin Day stores the chosen item's `id` and `name`. On each fetch it queries
  by `name` + `city` and picks the item with the stored `id`. If that `id`
  is gone, the entry raises an HA repair issue ("address not found") and
  keeps the last data and estimates.
- **Fetch times:** at startup, at 06:00, 12:00 and 18:30, and 30 minutes
  before each enabled reminder (the user's working rhythm). Errors are
  retried after 15 minutes, at most 4 times, then wait for the next fetch
  time. *Builder's call.*
- A fetch error never removes dates. The last good data stays, and the
  estimates continue from it.

### WCS (next iteration, not in v0.1)

Pick a WCS calendar entity, read events with `calendar.get_events`, one type
per event summary. Kept here so v0.1 does not block it.

- Bin Day keeps, per type, the next date and the future dates (for the
  interval and the 14-day strip).

## Estimates

Based on the hand-built version, made safe for sources other than Simbio
(*builder's calls*, after the review):

- Per type, Bin Day stores the last real date and the learned interval (HA
  `Store`, survives restarts).
- **Interval:** learned from the gap between two consecutive real dates of
  the same type, both from history and from future dates the source gives
  (Simbio gives one future date, so it learns only from history).
  Accepted range 7–35 days (covers weekly to monthly). Sources that publish
  many future dates teach the interval at once. Default 14 days. The user
  can change it per type.
- **When an estimate is made:** only when a type has no future real date in
  the look-ahead window (default 60 days).
- **How long an estimate is trusted:** at most 2 intervals after the last
  real date (for 14 days: 4 weeks). After that the type shows "no data", and
  no reminder is sent for it. This stops wrong reminders for a seasonal or
  stopped type. (The hand-built version used 120 days; the review showed
  that this sends reminders for months after a type stops.)
- **Per type option "Estimate dates"** (default on). Off = the type shows
  only real dates.
- An estimate is a suggestion, not a fact: the card marks it with "*", a
  hollow dot and the footnote; the reminder adds "*" and the footnote line.

## Types without data

- A type that never had a date (e.g. bio in winter, "ni odvoza") is hidden
  on the card and never causes a warning.
- A type that had dates and now has neither a real date nor a trusted
  estimate shows as "no data" in a small line on the card. The "data
  missing" warning (if enabled) is sent **once**, on the first evening this
  happens, not every evening. It is sent again only after the type has had
  a date again.
- If the source has failed since the last fetch time and no type has a date
  or trusted estimate, the card shows "No collection data. Check the
  source.", and the warning is sent once.

## Entities (one device per config entry, named by the user, e.g. "Odvoz smeti")

| Entity | State | Attributes |
|---|---|---|
| `sensor.<name>_<type>` per type | date (device class `date`) | `estimate`, `days`, `color`, `icon`, `source_date` |
| `sensor.<name>_next` | text: "Today: …", "Tomorrow: …" or "none" (translated) | `types`, `date`, `days` |
| `switch.<name>_reminder_evening` | reminder 1 on/off | — |
| `switch.<name>_reminder_morning` | reminder 2 on/off | — |
| `switch.<name>_reminder_day_before` | reminder 3 on/off | — |

The switches exist only when reminders are enabled in the options.

**Collection time** (one optional setting, *builder's call after the
review*): the latest hour the truck comes, e.g. 11:00.

- If it is set: the morning reminder and the card on the day say
  "until 11:00" ("do 11h"), and today's collection counts as done one hour
  later (12:00).
- If it is not set: no "until" text, and today's collection counts as done
  at 12:00.

## Setup (config flow)

1. (Next iteration: pick the source. In v0.1 the flow starts at step 2.)
2. Address: one text field ("street and house number"). Bin Day queries
   Simbio. No match: an error under the field. One or more matches: next
   step.
3. Pick the address from the matches, shown as "NAME, CITY" (up to 50).
   One address can be set up only once (unique ID = Simbio `id`).
4. Types: Bin Day lists the types of that address, including types with no
   date now (bio: "no collection now"). The user ticks the types to use
   (default: all), and can set per type: name, icon, colour. Defaults: the
   names above, icons `mdi:trash-can`, `mdi:recycle`, `mdi:leaf`, colours
   green #00a136, yellow #ffdd00, brown #8d6e63.
5. Name of the device (default "Bin Day – <NAME>"; the user can type e.g.
   "Odvoz smeti").

## Options (Configure button)

- Types: name, icon, colour, interval.
- Reminders: on/off (default off). Then:
  - notify targets (pick from `notify.*` actions, multiple)
  - evening before: time (default 19:00), on by default
  - morning of the day: time (default 07:30), on by default; text includes
    "until <hour>" only if the collection time is set
  - morning before: time (default 08:00), off by default
  - "data missing" warning: on/off (see "Types without data")
- Collection time (optional, see "Entities").

## Reminder texts

| Case | English | Slovenian |
|---|---|---|
| Day before | Tomorrow: Mixed waste, Packaging | Jutri odvoz: Mešani komunalni odpadki, Embalaža |
| On the day | Today: Mixed waste | Danes odvoz: Mešani komunalni odpadki |
| Estimate line | * estimate – no data from the source | * ocena – vir nima podatka |
| Data missing | Waste collection data is missing. Check the source. | Podatki o odvozu smeti manjkajo. Preveri vir podatkov. |

The language is the HA system language (fallback English). The title is the
device name.

## Card (`custom:bin-day-card`)

The approved design (mockup `skim-test-v3.html`, live as the `odvoz_smeti`
button-card template in the user's HA):

- Title (the device name) in HA's Heading style: `--ha-font-size-l`, medium
  weight, no icon.
- Headline: the next collection: icon in a tinted circle, full type name
  (wraps, never cut), "Friday, 13. 11. · in 4 days".
- One small line per other type: icon, name, short date, "in N days".
- A strip of 14 days: weekday letters and a round dot, coloured on
  collection days; today in bold; two types on one day share the dot half
  and half; an estimate is a hollow ring.
- Day before / day of: the whole card takes the colour of that type, with
  readable text (dark on light colours), and the headline reads
  "TOMORROW · …" / "TODAY · …".
- Footnote "* estimate – no data from the source" when any date is an
  estimate.
- No data at all: "No collection data. Check the source." / "Ni podatkov o
  odvozu. Preveri vir podatkov."
- Visual editor: pick the Bin Day device; optional "days in strip" (7–21,
  default 14) and "hide title".
- Badge: users add a normal HA entity badge for `sensor.<name>_next` with a
  visibility condition (state not "none"). The README shows how. Bin Day does
  not add badges to dashboards itself.

The reference implementation is the JavaScript of the author's earlier
hand-built dashboard card (not in this repository).

## Migration

The migration of the author's own Home Assistant is recorded in the
author's HA workspace, not in this repository.

## Repository and release

- Repository `urosgruber/ha-bin-day`, MIT licence, `hacs.json`, README in
  English (setup, badge example, screenshots).
- GitHub Actions: `hassfest`, HACS validation, tests.
- First release v0.1.0 as a HACS custom repository. Default-list submission
  later, after the user has used it for a while.
- **The user approved a full publish:** create the public repository
  `urosgruber/ha-bin-day` with `gh`, push, set up the Actions, and tag
  v0.1.0. Then install it in the user's HA from HACS (custom repository) and
  migrate the user's setup (see "Migration").
- The build happens in its own local folder, separate from the HA
  workspace. *Builder's call.*
- Before the first push, check that the repository holds no personal data:
  no addresses, entity IDs or phone names from the user's HA. Test data uses
  made-up addresses.

## Iterations

| Version | Content |
|---|---|
| v0.1 | Built-in Simbio source, estimates, entities, reminders, card, English and Slovenian, published, user's HA migrated |
| v0.2 (own plan) | WCS as a second source: source choice in setup, WCS calendar source. Default HACS list submission after that |

## Builder's calls (not discussed with the user)

- **Pick the address from a list.** Simbio returns every match, and WCS
  takes the first one, which can be the wrong house. Bin Day lets the user
  pick and stores Simbio's `id`.
- **One source interface** ("per type key, a list of future dates"), so the
  WCS source in v0.2 needs no changes elsewhere.
- **For v0.2: calendar entity instead of WCS sensors.** A WCS sensor's state
  depends on the user's own `value_template`, so its format is not reliable.
- **Own card, not a button-card template.** The integration ships a card file
  anyway, so a button-card dependency adds an install step and gives nothing.
- **Fetch times** 06:00, 12:00, 18:30 plus 30 minutes before each reminder,
  with up to 4 retries 15 minutes apart. About 4–6 requests per address per
  day.
- Estimate rules (see "Estimates") and "Types without data": changed from
  the user's version after the review, so that other sources and seasonal
  types do not get wrong reminders.
- One "collection time" setting instead of a separate "today ends" hour.
- Colour defaults: green #00a136, yellow #ffdd00, brown #8d6e63, blue
  #1e88e5, grey #757575, then repeat.
