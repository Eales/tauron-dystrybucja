# Tauron Dystrybucja

Home Assistant integration that reports planned and unplanned power outages for
a given address, using the public Tauron Dystrybucja web API (Poland).

Outages are exposed as a calendar, as an event that fires the moment Tauron
announces something new, and as plain sensors - so you can build your own
notifications without writing templates.

**Requires Home Assistant 2024.11.0 or newer.**

## Installation

### HACS (custom repository)

The integration is [awaiting review](https://github.com/hacs/default/pull/9324)
for the HACS default catalogue. Until that is merged, add it manually:

1. HACS > three-dot menu > `Custom repositories`
2. URL: `https://github.com/Eales/tauron-dystrybucja`, category: `Integration`
3. Install **Tauron Dystrybucja**, then restart Home Assistant

Updates arrive through HACS as usual once added.

### Manual

Copy `custom_components/tauron_dystrybucja` into your Home Assistant `config`
directory and restart.

## Configuration

1. `Settings` > `Devices & Services` > `Add Integration`
2. Search for `Tauron Dystrybucja`
3. Type at least 3 characters of the city name, then pick your city
4. Type at least 3 characters of the street name, then pick your street
5. Enter your house number

Add the integration several times to watch several addresses.

The Tauron API requires a street, so addresses in localities without named
streets cannot be configured.

### Polling interval

The API is polled every **60 minutes** by default; change it with the
integration's `Configure` button (15–1440 minutes).

Tauron announces planned outages days ahead, so polling more often gains almost
nothing. One address at the default interval is about 24 requests per day.

## Entities

Each address creates one device. Every fact about the *relevant* outage - the
ongoing one, or the next one when nothing is running - is a separate entity, so
it can go straight onto a dashboard.

| Entity | Type | Description |
| --- | --- | --- |
| `Status` | sensor (`enum`) | `No outages` / `Outage announced` / `Outage in progress`. Look here first. |
| `Outage start` | sensor (`timestamp`) | When it starts. |
| `Outage end` | sensor (`timestamp`) | When it ends. |
| `Duration` | sensor (`duration`, hours) | How long it lasts. |
| `Outage description` | sensor | What Tauron published. See the truncation note below. |
| `Announced outages` | sensor | How many outages fall in the next 30 days. |
| `Power outages` | calendar | Every outage as a calendar event; works with calendar triggers and the Calendar panel. |
| `New outage` | event | Fires once when Tauron announces an outage that was not known before. |
| `Outage in progress` | binary sensor (`problem`) | `on` while an outage is ongoing. |

### Attributes

`Outage start`, `Outage end`, `Duration`, `Outage description` and
`Outage in progress` all carry the same flat attributes, so an Entities card
with `type: attribute` rows needs no templating:

| Attribute | Meaning |
| --- | --- |
| `start` | Start of the outage |
| `end` | End of the outage |
| `description` | The published description |

Additionally:

- `Outage description` adds `full_description`. Home Assistant caps a state at
  255 characters and real descriptions do exceed that (264 observed), so the
  *state* may be truncated - `full_description` is always complete.
- `Announced outages` adds `outages`: the full list, each entry holding
  `start`, `end` and `description`.
- `New outage` carries `outage_id`, `start`, `end` and `description` when it
  fires.

## Dashboards

> **The entity IDs below are placeholders.** Copy your real ones from
> `Developer tools` > `States`, filtering by `tauron` - they depend on your
> address and your Home Assistant language.
>
> Home Assistant assigns an entity ID once, when the entity is first registered,
> and never changes it afterwards. If you upgraded from an older version,
> `Outage start` keeps its original ID (`..._najblizsze_wylaczenie`) even though
> it is now displayed as `Początek wyłączenia`. Renaming it is optional:
> entity settings > cog icon > `Entity ID`.

### Built-in cards, no templating

The standard **Calendar** card is the most readable view - a month or week grid,
with the full description on click:

```yaml
type: calendar
entities:
  - calendar.REPLACE_ME
```

A plain **Entities** card gives a compact summary:

```yaml
type: entities
title: Wyłączenia prądu
entities:
  - sensor.REPLACE_ME_status
  - sensor.REPLACE_ME_poczatek_wylaczenia
  - sensor.REPLACE_ME_koniec_wylaczenia
  - sensor.REPLACE_ME_czas_trwania
  - sensor.REPLACE_ME_opis_wylaczenia
```

### Markdown card: every outage at once

The cards above describe the next outage. To render *all* announced outages in
one block, this card reads the list from the counter sensor's attributes - one
entity ID to replace:

```yaml
type: markdown
content: |
  {% set encja = 'sensor.REPLACE_ME_zapowiedziane_wylaczenia' %}
  {% set dni = ['poniedziałek','wtorek','środa','czwartek','piątek','sobota','niedziela'] %}
  {% set lista = state_attr(encja, 'outages') or [] %}
  ## ⚡ Wyłączenia prądu
  {% if lista | count == 0 %}
  Brak zapowiedzianych wyłączeń na najbliższe 30 dni.
  {% else %}
  {% for o in lista %}
  {% set s = o.start if o.start is not string else as_datetime(o.start) %}
  {% set k = o.end if o.end is not string else as_datetime(o.end) %}
  {% set s = s | as_local %}
  {% set k = k | as_local %}
  {% set ile = (s.date() - now().date()).days %}
  {% if s <= now() and now() <= k %}
  ### 🔴 Trwa teraz — do {{ k.strftime('%H:%M') }}
  {% else %}
  ### {{ dni[s.weekday()] }} {{ s.strftime('%d.%m') }}, {{ s.strftime('%H:%M') }}–{{ k.strftime('%H:%M') }}
  {% if ile == 0 %}dzisiaj{% elif ile == 1 %}jutro{% elif ile == 2 %}pojutrze{% else %}za {{ ile }} dni{% endif %}
  {% endif %}

  {{ o.description }}
  {% if not loop.last %}

  ---
  {% endif %}
  {% endfor %}
  {% endif %}
```

Renders as:

```
## ⚡ Wyłączenia prądu

### poniedziałek 20.07, 08:00–16:00
pojutrze

Przykładowa ulica 2 do 6, 3 do 7, 11, 15 do 29, Inna 4 do 6,
Kolejna 3 do 7, Następna 1, 5, działka Nr 000/0.
```

## Automations

The integration never notifies you by itself - it only exposes entities. The
examples below are starting points; change the offset, the notify service and
the wording freely.

Two triggers answer different questions:

- the **event entity** - "Tauron has just announced something new", usually days
  ahead
- a **calendar trigger with an offset** - "it starts soon", with whatever lead
  time you choose

### Notify when a new outage is announced

```yaml
automation:
  - alias: "Tauron - nowe wyłączenie"
    triggers:
      - trigger: state
        entity_id: event.REPLACE_ME_nowe_wylaczenie
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.state not in ['unknown', 'unavailable'] }}"
    actions:
      - action: notify.persistent_notification
        data:
          title: "Uwaga - planowane wyłączenie prądu"
          message: >-
            {{ trigger.to_state.attributes.start | as_datetime | as_local
               | as_timestamp | timestamp_custom('%d.%m %H:%M') }}
            - {{ trigger.to_state.attributes.end | as_datetime | as_local
               | as_timestamp | timestamp_custom('%H:%M') }}
            {{ trigger.to_state.attributes.description }}
```

### Remind before an outage starts

`offset` decides how early you are warned: `-02:00:00` is two hours,
`-1 day, 0:00:00` a day ahead.

```yaml
automation:
  - alias: "Tauron - wkrótce wyłączenie"
    triggers:
      - trigger: calendar
        entity_id: calendar.REPLACE_ME
        event: start
        offset: "-02:00:00"
    actions:
      - action: notify.persistent_notification
        data:
          title: "Wkrótce nie będzie prądu"
          message: "{{ trigger.calendar_event.description }}"
```

## Notes

- **Only outages that reach your address are reported.** The API answers with
  every outage in the area, so the integration keeps an item only when the
  address point Tauron assigned to your house is on the item's list of affected
  points. Area-wide faults come without such a list; those are kept only when
  their text names your town. Skipped items are logged at `INFO` level by
  `custom_components.tauron_dystrybucja.coordinator`.
- **Still read the description.** For an area-wide fault the town name is the
  only hint, so the description remains the final word on whether your street
  is affected. Every card here shows it for that reason.
- `New outage` stays silent on the first refresh after a restart, so restarting
  Home Assistant never replays announcements you already saw.
- Tauron reuses one outage ID across separate time slots of the same works. Each
  slot is tracked as its own occurrence, so none are lost.
- Diagnostics can be downloaded from the device page; the house number is
  redacted.

## Licence

MIT - see [LICENSE](LICENSE).
