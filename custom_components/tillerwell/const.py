"""Constants for the Tillerwell Hearth integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "tillerwell"

CONF_PASSWORD = "password"
CONF_EXTERNAL_TEMP_ENTITY = "external_temperature_entity"

MANUFACTURER = "Tillerwell"
MODEL = "Hearth"

# The box serves plain HTTP on port 80 and is cheap to poll (the iOS app polls every 2 s).
SCAN_INTERVAL = timedelta(seconds=5)
# Slow-changing data is read less often than the status document.
INFO_INTERVAL = timedelta(minutes=1)
TIMERS_INTERVAL = timedelta(minutes=1)
# The box only trusts an external temperature for 90 s, so refresh well inside that.
EXTERNAL_TEMP_REFRESH = timedelta(seconds=30)
# Do not hammer the box with clock fixes while it is refusing them.
CLOCK_SYNC_RETRY = timedelta(seconds=60)

# `state` values reported by the firmware.
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_LATE_RUN = "late-run"
STATE_COOLDOWN = "cooldown"
STATE_FINAL_SHUTDOWN = "final-shutdown"
STATES = [STATE_IDLE, STATE_RUNNING, STATE_LATE_RUN, STATE_COOLDOWN, STATE_FINAL_SHUTDOWN]
ACTIVE_STATES = (STATE_RUNNING, STATE_LATE_RUN)

# Limits the firmware enforces (components/at_app/at_app.c).
MIN_AUTO_TEMP = 0
MAX_AUTO_TEMP = 30
MIN_PREHEAT_MINUTES = 1
MAX_PREHEAT_MINUTES = 600
MIN_PREHEAT_COOLANT = 40
MAX_PREHEAT_COOLANT = 95
MAX_FREEZE_TARGET = 10

SERVICE_SET_TIMERS = "set_timers"
SERVICE_SET_EXTERNAL_TEMPERATURE = "set_external_temperature"
ATTR_TIMERS = "timers"
