"""mqtt_publisher.py — Build MQTT payloads and publish with HA auto-discovery.

Design
------
* One combined JSON state is published to ``mbus/{device_id}/state``.
* Individual HA sensor discovery configs are published (retained) to
  ``homeassistant/sensor/mbus_{device_id}_{record_id}/config``.
* Records with unit "Manufacturer specific" are skipped entirely.
* Volume device_class (``water`` vs ``gas``) is inferred from SlaveInfo.medium.

Unit strings
------------
libmbus formats volume as ``Volume (%s m^3)`` where the prefix comes from
``mbus_unit_prefix(exponent)``.  The prefix table (from mbus-protocol.c):

  exponent  prefix    → unit string          HA unit
  -6        "my"      → "Volume (my m^3)"    µL  (micro-litre)
  -5        "1e-5 "   → "Volume (1e-5 m^3)"  0.01 mL
  -4        "1e-4 "   → "Volume (1e-4 m^3)"  0.1 mL
  -3        "m"       → "Volume (m m^3)"     1 L  ← most residential water meters
  -2        "1e-2 "   → "Volume (1e-2 m^3)"  10 L
  -1        "1e-1 "   → "Volume (1e-1 m^3)"  100 L
   0        ""        → "Volume ( m^3)"      1 m³
   1        "10 "     → "Volume (10 m^3)"    10 m³

NOTE: "Volume (0.1 m^3)" does NOT appear in libmbus output — the correct
string for 0.1 m³ is "Volume (1e-1 m^3)".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import paho.mqtt.client as mqtt

from mbus_parser import DataRecord, SlaveInfo

_LOGGER = logging.getLogger(__name__)

# Regex: only allow characters that are safe in MQTT topic levels and HA unique_id.
# Anything else is replaced with '_'.
_UNSAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")

# HA MQTT discovery 'origin' block (required since 2023.8).
# Identifies the integration/add-on that published the discovery message.
_ORIGIN = {
    "name": "M-Bus MQTT",
    "sw": "1.0.0",
    "url": "https://git.int.lastsys.com/stefan/ha-mbus",
}


def sanitise_device_id(raw: str) -> str:
    """Return a version of *raw* that is safe to embed in MQTT topic strings.

    Replaces any character outside ``[a-zA-Z0-9_-]`` with ``_``.
    MQTT wildcards ``#`` and ``+``, spaces, slashes, and null bytes are all
    caught by this pattern.
    """
    safe = _UNSAFE_ID_RE.sub("_", raw.strip())
    return safe if safe else "unknown"


# ---------------------------------------------------------------------------
# Unit mapping
# ---------------------------------------------------------------------------
# Each entry: (unit_substring, device_class, ha_unit, state_class)
# Matched via ``unit_string.startswith(substring)`` in order.
#
# ORDERING RULES:
#  1. More-specific prefixes must appear BEFORE their prefixes.
#     e.g. "Volume flow" MUST precede "Volume" because
#     "Volume flow".startswith("Volume") is True.
#  2. Multiplied variants ("Energy (10 Wh)") must precede plain ("Energy (Wh)").
#
# device_class=None  → generic sensor without HA device class.
# ha_unit=None       → no unit_of_measurement (e.g. dimensionless or date strings).
# "VOLUME"           → resolved at runtime to "water" or "gas" via _volume_device_class().

_UNIT_MAP: list[tuple[str, str | None, str | None, str]] = [
    # ── Volume flow  (MUST precede "Volume" catch-all) ────────────────────
    ("Volume flow",              None,      "m³/h",  "measurement"),

    # ── Volume — libmbus unit strings (VIF 0x10-0x17) ────────────────────
    # Exact strings from mbus_unit_prefix(): do NOT change these.
    ("Volume (my m^3)",         "VOLUME",   "µL",    "total_increasing"),  # 10⁻⁶ m³
    ("Volume (1e-5 m^3)",       "VOLUME",   "µL",    "total_increasing"),  # 10⁻⁵ m³
    ("Volume (1e-4 m^3)",       "VOLUME",   "µL",    "total_increasing"),  # 10⁻⁴ m³
    ("Volume (m m^3)",          "VOLUME",   "L",     "total_increasing"),  # 10⁻³ m³ = 1 L ← common
    ("Volume (1e-2 m^3)",       "VOLUME",   "L",     "total_increasing"),  # 10⁻² m³ = 10 L
    ("Volume (1e-1 m^3)",       "VOLUME",   "m³",    "total_increasing"),  # 10⁻¹ m³ = 100 L
    ("Volume ( m^3)",           "VOLUME",   "m³",    "total_increasing"),  # 1 m³  (space prefix)
    ("Volume (m^3)",            "VOLUME",   "m³",    "total_increasing"),  # 1 m³  (no space, alt)
    ("Volume (10 m^3)",         "VOLUME",   "m³",    "total_increasing"),  # 10 m³
    ("Volume (100 m^3)",        "VOLUME",   "m³",    "total_increasing"),  # 100 m³
    ("Volume (k m^3)",          "VOLUME",   "m³",    "total_increasing"),  # 10³ m³ = 1000 m³
    ("Volume",                  "VOLUME",   None,    "total_increasing"),  # catch-all

    # ── Energy ────────────────────────────────────────────────────────────
    # libmbus: "Energy (%s Wh)" with prefix from mbus_unit_prefix(n-3)
    # Multiplied variants listed first so the shorter prefix doesn't shadow them.
    ("Energy (m Wh)",           "energy",   "Wh",    "total_increasing"),  # milli-Wh
    ("Energy (1e-2 Wh)",        "energy",   "Wh",    "total_increasing"),  # 0.01 Wh
    ("Energy (1e-1 Wh)",        "energy",   "Wh",    "total_increasing"),  # 0.1 Wh
    ("Energy (Wh)",             "energy",   "Wh",    "total_increasing"),
    ("Energy (10 Wh)",          "energy",   "Wh",    "total_increasing"),
    ("Energy (100 Wh)",         "energy",   "Wh",    "total_increasing"),
    ("Energy (mWh)",            "energy",   "mWh",   "total_increasing"),  # alt form
    ("Energy (kWh)",            "energy",   "kWh",   "total_increasing"),
    ("Energy (10 kWh)",         "energy",   "kWh",   "total_increasing"),
    ("Energy (100 kWh)",        "energy",   "kWh",   "total_increasing"),
    ("Energy (MWh)",            "energy",   "MWh",   "total_increasing"),
    ("Energy (MJ)",             "energy",   "MJ",    "total_increasing"),
    ("Energy (GJ)",             "energy",   "GJ",    "total_increasing"),
    ("Energy",                  "energy",   None,    "total_increasing"),  # catch-all

    # ── Power ─────────────────────────────────────────────────────────────
    ("Power (mW)",              "power",    "mW",    "measurement"),
    ("Power (W)",               "power",    "W",     "measurement"),
    ("Power (10 W)",            "power",    "W",     "measurement"),
    ("Power (kW)",              "power",    "kW",    "measurement"),
    ("Power (MW)",              "power",    "MW",    "measurement"),
    ("Power",                   "power",    None,    "measurement"),       # catch-all

    # ── Temperature ──────────────────────────────────────────────────────
    ("Flow temperature",        "temperature", "°C", "measurement"),
    ("Return temperature",      "temperature", "°C", "measurement"),
    ("External temperature",    "temperature", "°C", "measurement"),
    ("Temperature Difference",  "temperature", "°C", "measurement"),
    ("Temperature",             "temperature", "°C", "measurement"),      # catch-all

    # ── Pressure ─────────────────────────────────────────────────────────
    ("Pressure (mbar)",         "pressure", "mbar",  "measurement"),
    ("Pressure (bar)",          "pressure", "mbar",  "measurement"),
    ("Pressure",                "pressure", "mbar",  "measurement"),      # catch-all

    # ── Time / Duration ──────────────────────────────────────────────────
    ("Operating time (minutes)", "duration", "min",  "total_increasing"),
    ("On time (minutes)",        "duration", "min",  "total_increasing"),
    ("Operating time (hours)",   "duration", "h",    "total_increasing"),
    ("On time (hours)",          "duration", "h",    "total_increasing"),
    ("Operating time",           "duration", "s",    "total_increasing"),
    ("On time",                  "duration", "s",    "total_increasing"),

    # ── Electrical ────────────────────────────────────────────────────────
    ("Current",                 "current",  "A",     "measurement"),
    ("Voltage",                 "voltage",  "V",     "measurement"),

    # ── Date / time (string values) ───────────────────────────────────────
    ("Time Point (time & date)", "timestamp", None,  "measurement"),
    ("Time Point (date)",        None,         None, "measurement"),
    ("Time Point",               None,         None, "measurement"),

    # ── HCA (Heat Cost Allocator) ─────────────────────────────────────────
    ("Units for H.C.A.",        None,       "HCA",   "total_increasing"),

    # ── Fabrication/info ──────────────────────────────────────────────────
    ("Fabrication number",      None,        None,   "measurement"),
]

# Units that should produce no HA discovery config at all.
_SKIP_UNITS = {"Manufacturer specific", "Reserved", ""}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_device_registry(
    slave_info: SlaveInfo,
    device_name: str,
    device_area: str,
) -> dict[str, Any]:
    """Return the ``device`` block used in all discovery payloads."""
    name = (
        device_name.strip()
        if device_name and device_name.strip()
        else f"M-Bus {slave_info.medium or 'meter'} {slave_info.id}"
    )
    device: dict[str, Any] = {
        "identifiers": [f"mbus_{slave_info.id}"],
        "name": name,
        "manufacturer": slave_info.manufacturer or None,
        "model": f"M-Bus meter v{slave_info.version}" if slave_info.version else "M-Bus meter",
        "sw_version": slave_info.version or None,
    }
    # Prune None values — HA ignores unknown keys but prefer clean payloads.
    device = {k: v for k, v in device.items() if v is not None}

    if device_area and device_area.strip():
        device["suggested_area"] = device_area.strip()

    return device


def build_discovery_payloads(
    records: list[DataRecord],
    slave_info: SlaveInfo,
    device_id: str,
    device_registry: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of ``(topic, payload)`` pairs for HA MQTT discovery."""
    result: list[tuple[str, dict[str, Any]]] = []
    for rec in records:
        item = _build_one_discovery(rec, slave_info, device_id, device_registry)
        if item is not None:
            result.append(item)
    return result


def publish_discovery(
    client: mqtt.Client,
    payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    """Publish all discovery configs with retain=True."""
    for topic, payload in payloads:
        client.publish(topic, json.dumps(payload), retain=True, qos=1)
        _LOGGER.debug("Discovery → %s", topic)


def publish_state(
    client: mqtt.Client,
    device_id: str,
    slave_info: SlaveInfo,
    records: list[DataRecord],
) -> None:
    """Publish the combined JSON state for all data records."""
    payload = build_state_payload(slave_info, records)
    topic = f"mbus/{device_id}/state"
    client.publish(topic, json.dumps(payload), retain=True, qos=1)
    _LOGGER.debug("State     → %s", topic)


def build_state_payload(
    slave_info: SlaveInfo,
    records: list[DataRecord],
) -> dict[str, Any]:
    """Build the JSON state dict (all records in one payload)."""
    records_dict: dict[str, Any] = {}
    for rec in records:
        records_dict[str(rec.id)] = {
            "value": rec.value,
            "unit": rec.unit,
            "function": rec.function,
            "storage": rec.storage_number,
        }
        if rec.tariff is not None:
            records_dict[str(rec.id)]["tariff"] = rec.tariff
        if rec.device is not None:
            records_dict[str(rec.id)]["device"] = rec.device

    return {
        "device_id": slave_info.id,
        "manufacturer": slave_info.manufacturer,
        "medium": slave_info.medium,
        "timestamp": records[0].timestamp if records else "",
        "records": records_dict,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_unit_info(
    unit: str,
    medium: str,
) -> tuple[str | None, str | None, str] | None:
    """Return ``(device_class, ha_unit, state_class)`` for *unit*, or None to skip."""
    if unit in _SKIP_UNITS:
        return None

    for prefix, device_class, ha_unit, state_class in _UNIT_MAP:
        if unit.startswith(prefix):
            if device_class == "VOLUME":
                device_class = _volume_device_class(medium)
            return device_class, ha_unit, state_class

    # Unrecognised unit — produce a generic diagnostic sensor rather than
    # silently dropping the record.
    return None, unit, "measurement"


def _volume_device_class(medium: str) -> str:
    """Infer HA device_class from M-Bus medium string."""
    m = medium.lower()
    if "gas" in m:
        return "gas"
    if "water" in m or "warm" in m or "hot" in m or "cold" in m:
        return "water"
    # Heat/cooling meters also report volume
    if "heat" in m or "cool" in m or "energy" in m:
        return "water"
    return "water"  # safe default


def _record_label(rec: DataRecord) -> str:
    """Human-readable label for a data record, used as the sensor name suffix."""
    unit = rec.unit
    # Exact-prefix label overrides
    _UNIT_LABEL: dict[str, str] = {
        "Volume flow":              "Flow rate",
        "Volume":                   "Volume",        # catches all Volume (…) variants
        "Operating time":           "Operating time",
        "On time":                  "On time",
        "Flow temperature":         "Flow temperature",
        "Return temperature":       "Return temperature",
        "External temperature":     "Temperature",
        "Temperature Difference":   "Temperature difference",
        "Time Point (time & date)": "Date/time",
        "Time Point (date)":        "Date",
        "Time Point":               "Date/time",
        "Units for H.C.A.":         "HCA",
    }
    lbl = None
    for prefix, label in _UNIT_LABEL.items():
        if unit.startswith(prefix):
            lbl = label
            break
    if lbl is None:
        # Fall back: strip any parenthesised suffix, use the base word
        lbl = unit.split("(")[0].strip() or unit

    # Qualify historic / tariff records
    qualifiers: list[str] = []
    if rec.storage_number > 0:
        qualifiers.append(f"storage {rec.storage_number}")
    if rec.tariff is not None:
        qualifiers.append(f"tariff {rec.tariff}")
    if qualifiers:
        lbl = f"{lbl} ({', '.join(qualifiers)})"

    return lbl


def _icon_for(device_class: str | None, medium: str) -> str | None:
    m = medium.lower()
    if device_class == "water" or "water" in m:
        return "mdi:water"
    if device_class == "gas" or "gas" in m:
        return "mdi:gas-cylinder"
    if device_class == "energy":
        return "mdi:lightning-bolt"
    if device_class == "power":
        return "mdi:flash"
    if device_class == "temperature":
        return "mdi:thermometer"
    if device_class == "pressure":
        return "mdi:gauge"
    if device_class == "duration":
        return "mdi:timer-outline"
    return None


def _build_one_discovery(
    rec: DataRecord,
    slave_info: SlaveInfo,
    device_id: str,
    device_registry: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Build a single ``(topic, payload)`` pair, or return None to skip."""
    info = _resolve_unit_info(rec.unit, slave_info.medium)
    if info is None:
        return None  # skip "Manufacturer specific" etc.

    device_class, ha_unit, state_class = info
    label = _record_label(rec)

    # Derive the sensor name from device name + label
    device_name: str = device_registry.get("name", f"M-Bus {slave_info.id}")
    sensor_name = f"{device_name} {label}"

    # Use the sanitised device_id to build MQTT-safe topic strings
    safe_id = sanitise_device_id(device_id)
    unique_id = f"mbus_{safe_id}_{rec.id}"
    state_topic = f"mbus/{safe_id}/state"
    avail_topic = f"mbus/{safe_id}/availability"
    disc_topic = f"homeassistant/sensor/{unique_id}/config"

    payload: dict[str, Any] = {
        "name": sensor_name,
        "unique_id": unique_id,
        "state_topic": state_topic,
        "value_template": "{{ value_json.records['" + str(rec.id) + "'].value }}",
        "availability_topic": avail_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "state_class": state_class,
        "device": device_registry,
        "origin": _ORIGIN,
    }

    if device_class:
        payload["device_class"] = device_class

    if ha_unit:
        payload["unit_of_measurement"] = ha_unit

    icon = _icon_for(device_class, slave_info.medium)
    if icon:
        payload["icon"] = icon

    # Diagnostic sensors (no recognised device class) get entity_category
    if device_class is None and rec.unit not in ("", "Manufacturer specific"):
        payload["entity_category"] = "diagnostic"

    return disc_topic, payload
