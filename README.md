# Tillerwell for Home Assistant

Control an Autoterm diesel heater from Home Assistant through a **Tillerwell Hearth** box, locally over your Wi-Fi. No cloud, no account.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Tillerwell&repository=Tillerwell-for-Home-Assistant&category=integration)

*Hearth is a Tillerwell product. It works with Autoterm heaters and is not made by or affiliated with Autoterm.*

## What you get

| Entity | What it does |
|---|---|
| **Thermostat** (`climate`) | Heat arms the box's Auto thermostat, Off stops the heater. Two set points, a low and a high, form the comfort band. Shows the temperature the box is using and whether the burner is on |
| **Heater** (switch) | On starts a timed preheat of the saved duration, off stops the heater properly |
| **Start preheat / Start thermostat / Ventilate / Stop** (buttons) | One-shot commands |
| **State, Coolant temperature, Controller temperature, Thermostat temperature, Run time** | Live status |
| **Battery voltage** | The supply voltage the heater reports, always on (exact value from extended telemetry when that is switched on) |
| **Fault, Last fault** and a **Fault** problem sensor | Heater fault names as reported by the heater |
| **Next timer, Last timer event, Timers** | The schedule stored on the box |
| **Prevent freezing** (switch + target) | Starts the heater at the frost floor even if you pressed Stop |
| **Preheat duration, Preheat coolant limit, Start/Stop hysteresis** (numbers) | The box's saved settings |
| **Bypass, Use external temperature** (switches) | Bypass makes the box relay only and never send a command |
| Diagnostics | Heater and controller link, stale data, Wi-Fi signal, clock set, running without controller |
| Extended telemetry (off by default) | Flame, board and overheat temperatures, fuel pump, output. Enable the *Extended telemetry probing* switch on the box first (experimental) |

Everything is polled from the box every 5 seconds.

## Install with HACS

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/Tillerwell/Tillerwell-for-Home-Assistant` with the category **Integration**.
3. Find **Tillerwell Hearth** in HACS, download it, and **restart Home Assistant**.

Or use the button at the top of this page.

Manual install: copy `custom_components/tillerwell` into `config/custom_components/` and restart.

Needs Home Assistant 2025.1 or newer.

## Set up

First get the box onto your Wi-Fi (the Tillerwell app, or the box's own `Hearth-xxxxxx` setup network). Then:

- **Discovery:** the box announces itself, so Home Assistant offers it under *Settings → Devices & services*. Click **Add**. Choosing *Add integration → Tillerwell Hearth* also scans your network for a few seconds and lists any boxes it finds.
- **By hand:** if nothing is found (or you pick *Enter an address instead*), enter the box's address: its name ending in `.local` (for example `hearth-a1b2c3.local`) or its IP address. Give the box a fixed IP address in your router if you can; if it changes, use *Reconfigure* on the integration to update it.
- The integration does not support the box's **web password**. Leave it unset on the box.

## Thermostat

The box runs the thermostat itself, so it keeps working if Home Assistant is off. The two set points are a comfort band, not the switching points: the heater starts at **low minus the start hysteresis** and stops at **high plus the stop hysteresis** (both are numbers on the device). The set points are whole degrees from 0 to 30.

If the heater was started some other way (the Comfort Controller, a timer, the Heater switch), the thermostat shows **Heat** while it runs. Setting **Off** switches Auto off first and then stops the heater, because otherwise the box would start it again.

### Use a room sensor you already have

The box only knows the temperature at the controller. In the integration's **Configure** dialog, pick any Home Assistant temperature sensor (a Zigbee sensor in the cabin, say). It is sent to the box every 30 seconds, converted to °C, and used for the thermostat while **Use external temperature** is on. If you would rather push it from an automation, use `tillerwell.set_external_temperature`; the box only trusts a value for 90 seconds.

## Timers

The box stores up to 32 timers and fires them itself, so they run with Home Assistant and your phone off. Read them from the **Timers** sensor's `timers` attribute and replace the whole list with `tillerwell.set_timers`:

```yaml
action: tillerwell.set_timers
target:
  entity_id: climate.hearth_a1b2c3
data:
  timers:
    - mode: preheat            # or thermostat
      start: "06:30:00"
      duration_min: 60         # 20 to 480
      days: [mon, tue, wed, thu, fri]
    - mode: thermostat
      start: "18:00:00"
      duration_min: 180
      date: "2026-12-24"       # a one-off instead of days
      low_c: 19                # thermostat timers only
      high_c: 22
```

Optional keys: `id` (a new one is made if you leave it out) and `enabled`. An empty list clears all timers. The box checks every rule (30 minutes between runs, no one-off in the past, and so on) and tells you why if it refuses.

Timers only fire when the box knows the time and time zone. A boat with no internet has no network time, and the box has no battery clock, so **while the box says its clock is not set, this integration sets its time zone (if it has none) and its time from Home Assistant**. The box ignores this once it has network time.

## Other services

| Service | Use |
|---|---|
| `tillerwell.set_timers` | Replace the timer list (above) |
| `tillerwell.set_external_temperature` | Send a room temperature in °C |

## Security

The box speaks plain HTTP, and anyone on the same Wi-Fi can talk to it. That is fine on a boat's own network; do not expose the box to the internet. This integration only talks to the box, never to a cloud.

## Already using MQTT?

The firmware can also publish to Home Assistant over MQTT. You do not need both: they create separate sets of entities for the same box. Use this integration for the thermostat, services and local control, or MQTT, and switch the other off (turn MQTT off on the box's Setup tab, or remove the MQTT device).

## Troubleshooting

- **Cannot connect:** open `http://<box address>` in a browser from the same network as Home Assistant. `.local` names need mDNS between Home Assistant and the box; if that fails, use the IP address.
- **Commands do nothing or error:** the box refuses commands while **Bypass** is on or its queue is full. Turn Bypass off.
- **Temperatures unknown:** the box has not heard the heater (see the *Heater link* diagnostic) or its data is stale.
- Turn on debug logging for `custom_components.tillerwell` to see what is being sent.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check . && pytest
```

The box's API is described in the firmware repository. The tests run the integration against a mocked box using Home Assistant's own test harness.

## Licence

MIT, see [LICENSE](LICENSE).
