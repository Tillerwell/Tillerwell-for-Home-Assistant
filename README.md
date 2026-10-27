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
| **Fuel used, Fuel cost** (total and today), **Fuel rate** | Estimated diesel use, see below. Needs RoamOS 1.0011 or later on the box |
| **Power level, Heater mode, Burner hours, Starts** | What the heater says it is doing, and how much it has run (hours at low, middle and high are diagnostics) |
| **Fuel price, Fuel calibration factor** (numbers) | Price per litre in your Home Assistant currency, and the estimate's correction. The four fuel rates are there too, disabled by default |
| **Fault, Last fault** and a **Fault** problem sensor | Heater fault names as reported by the heater |
| **Next timer, Last timer event, Timers** | The schedule stored on the box |
| **Prevent freezing** (switch + target) | Starts the heater at the frost floor even if you pressed Stop |
| **Preheat duration, Preheat coolant limit, Start/Stop hysteresis** (numbers) | The box's saved settings |
| **Bypass, Use external temperature** (switches) | Bypass makes the box relay only and never send a command |
| Diagnostics | Heater and controller link, stale data, Wi-Fi signal, clock set, running without controller, last command; line errors, unanswered commands and box uptime are disabled by default |
| Extended telemetry (off by default) | Flame, board and overheat temperatures, fuel pump, output. Enable the *Extended telemetry probing* switch on the box first (experimental). These show *Unavailable* until the heater actually answers; many heaters on a live controller bus never do |

Everything is polled from the box every 5 seconds. When there is nothing to report, Fault shows *No fault* and the notes and Last command show *None*. Buttons show *Unknown* until first pressed, which is how Home Assistant shows every button.

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

## Fuel and cost

The heater doesn't report its fuel pump to the box, so the box estimates fuel: time at each power level times a litres-per-hour rate (Autoterm Flow 5 spec-sheet figures by default) times the calibration factor. It keeps the totals and a daily log itself, so nothing is lost while Home Assistant is off.

- **Fuel used (estimated)** is a total that only goes up, in litres, so Home Assistant keeps long-term statistics for it: use it in a statistics graph or a utility meter for weekly or monthly figures.
- Set **Fuel price** to what you pay per litre. The box adds up the cost as it goes, so a new price applies from then on and past costs stay as they were.
- To calibrate, fill the tank to the top, note **Fuel used**, run the heater for a few days, fill up again and divide the litres that went in by the change in **Fuel used**. Multiply **Fuel calibration factor** by that. The Tillerwell app does the sum for you.

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
