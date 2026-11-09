# Bin Day

Bin Day shows your waste collection dates in Home Assistant. It has a card, sensors, estimates and reminders. It reads the dates from Simbio.

**Povzetek (SL):** Bin Day prikaže datume odvoza odpadkov v Home Assistantu. Podatke prebere s spletnega mesta Simbio (območje Celje). Dodate naslov, izberete vrste odpadkov in dobite kartico, senzorje in opomnike. Vmesnik je v slovenščini in angleščini.

## What it does

- It finds your address on simbio.si and reads the collection dates.
- It creates one date sensor for each waste type.
- It creates one "next collection" sensor.
- It estimates future dates when Simbio has not published them yet.
- It sends reminders to your notify targets.
- It adds a card, `bin-day-card`. The integration registers the card for you.

## Requirements

- Home Assistant 2026.11 or newer.
- An address in the Simbio region (Celje).

## Install

1. Open HACS.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/urosgruber/ha-bin-day` with the category **Integration**.
4. Install **Bin Day** and restart Home Assistant.

## Set up

1. Go to **Settings > Devices & services > Add integration** and choose **Bin Day**.
2. Type part of your address and search.
3. Pick the exact address from the list.
4. Choose the waste types you want.
5. For each type, set the name, the icon and the colour.
6. Enter a device name.

## Entities

**Type sensors.** Each type has a sensor with the device class `date`. Its state is the next collection date. Its attributes are `estimate`, `status`, `days`, `dates`, `color`, `icon`, `type_name`, `order`, `last_real` and `interval`.

**Next sensor.** The entity id is `sensor.<device name>_next_collection`. The state is "Today: ..." or "Tomorrow: ...". In Slovenian it is "Danes: ..." or "Jutri: ...". When nothing is due soon, the state is the text `none`.

**Reminder switches.** Each reminder has a switch entity.

## Card

The integration registers the card automatically. You do not add a resource. You do not need button-card.

```yaml
type: custom:bin-day-card
device_id: <your Bin Day device>
strip_days: 14
hide_title: false
```

| Option | Meaning | Default |
|---|---|---|
| `device_id` | The Bin Day device. If you omit it, the card uses the first one. | first device |
| `strip_days` | Number of days in the strip, from 7 to 21. | 14 |
| `hide_title` | Hides the card title. | false |

You can also add the card with the visual editor.

The card leaves out a type that has no date to show. When no type has a date, the card shows "No collection data. Check the source."

## Badge

This badge shows the next collection. It hides when the state is `none`.

```yaml
badges:
  - type: entity
    entity: sensor.<name>_next_collection
    color: green
    visibility:
      - condition: state
        entity: sensor.<name>_next_collection
        state_not: "none"
```

## Options

Open the integration and choose **Configure**.

- **Each type:** name, icon, colour, estimate on or off, and interval.
- **Reminders:** enable them and choose the notify targets. Each reminder has its own switch.
  - Evening: 19:00, on.
  - Morning of collection: 07:30, on.
  - Morning before collection: 08:00, off.
- **General:**
  - A warning when data is missing.
  - An optional collection time. The card then shows "until HH:MM". Today counts as done 1 hour after that time. Without a collection time, today is done at 12:00.

## Estimates

Bin Day learns the interval between real dates. It accepts gaps of 7 to 35 days. The default is 14 days. It trusts an estimate for 2 intervals after the last real date.

The card marks an estimate with `*`, a hollow dot and a footnote.

## Data source

Bin Day reads simbio.si. It fetches the data at 06:00, 12:00 and 18:30. It also fetches at startup and 30 minutes before each reminder. If a fetch fails, Bin Day keeps all existing dates. An error never deletes a date.

## Limitations

- Simbio is the only source in v0.1.
- Support for Waste Collection Schedule as an optional source is planned.

## Languages

The card, the messages and the setup screens use English or Slovenian. Bin Day follows the Home Assistant system language.

## Licence

MIT. See `LICENSE`.
