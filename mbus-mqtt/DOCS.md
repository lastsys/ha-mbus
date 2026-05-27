# M-Bus MQTT — Add-on Documentation

## Overview

This add-on connects to a single M-Bus device via a TCP gateway, reads all available data
records, and publishes them to MQTT. Home Assistant sensors are created automatically via
MQTT auto-discovery — no YAML configuration required.

## Prerequisites

1. **Mosquitto broker add-on** must be installed and running. The add-on fetches MQTT
   credentials automatically from the Home Assistant Supervisor.
2. An M-Bus device accessible via a TCP gateway (e.g. a Relay/MBUS-LAN converter,
   a DSMR P1-to-TCP bridge, or any device running `mbus-httpd`).

## Configuration Options

### `mbus_host` (required)
The hostname or IP address of the M-Bus TCP gateway.

Example: `192.168.1.100`

### `mbus_port`
The TCP port on the gateway. Default: `10001`.

### `mbus_address`
The primary M-Bus address of the target device. Valid range: 0–250. Default: `1`.

To find the address of your device, check the gateway documentation or scan the bus
with `mbus-tcp-scan <host> <port>` if you have libmbus installed locally.

### `poll_interval`
How often (in seconds) to read the device. Default: `60`. Minimum: `10`. Maximum: `3600`.

Some meters (e.g. ultrasonic water meters) accept only a limited number of reads per day —
check your meter's datasheet and set this accordingly.

### `device_name` (optional)
A friendly name for the device as it appears in Home Assistant. If omitted, the name is
derived from the M-Bus medium and serial number (e.g. `M-Bus Cold water 12345678`).

### `device_area` (optional)
Assign the device to a Home Assistant area (e.g. `Basement`, `Kitchen`).

## MQTT Topics

Once running, the add-on publishes to these topics:

| Topic | Payload | Notes |
|-------|---------|-------|
| `mbus/{id}/state` | JSON | All data records. Retained. |
| `mbus/{id}/availability` | `online` / `offline` | Last-will testament. Retained. |
| `homeassistant/sensor/mbus_{id}_{n}/config` | JSON | HA discovery. Retained. |

Where `{id}` is the meter serial number (e.g. `12345678`) and `{n}` is the data record index.

### State payload example

```json
{
  "device_id": "12345678",
  "manufacturer": "KAW",
  "medium": "Cold water",
  "timestamp": "2026-05-27T14:29:35Z",
  "records": {
    "1": {"value": 7712, "unit": "Volume (m m^3)", "function": "Instantaneous value", "storage": 0},
    "4": {"value": 169131, "unit": "Operating time (minutes)", "function": "Instantaneous value", "storage": 0}
  }
}
```

## Supported Unit Mappings

| M-Bus unit | HA device class | HA unit |
|------------|-----------------|---------|
| Volume (m m^3) | `water` / `gas` | `L` |
| Volume (m^3) | `water` / `gas` | `m³` |
| Energy (Wh) | `energy` | `Wh` |
| Energy (kWh) | `energy` | `kWh` |
| Energy (MWh) | `energy` | `MWh` |
| Energy (MJ) | `energy` | `MJ` |
| Energy (GJ) | `energy` | `GJ` |
| Power (W) | `power` | `W` |
| Power (kW) | `power` | `kW` |
| Flow temperature | `temperature` | `°C` |
| Return temperature | `temperature` | `°C` |
| External temperature | `temperature` | `°C` |
| Temperature Difference | `temperature` | `°C` |
| Pressure | `pressure` | `mbar` |
| Operating time / On time | `duration` | `min` |
| Current | `current` | `A` |
| Voltage | `voltage` | `V` |
| Time Point (date) | *(none)* | *(none)* |
| Manufacturer specific | *skipped* | — |

Volume records use `water` device class if the M-Bus medium contains "water", and `gas`
if the medium contains "gas". Other media fall back to a generic sensor without device class.

## Troubleshooting

### No entities appear in Home Assistant
- Check the add-on log for errors (Settings → Add-ons → M-Bus MQTT → Log)
- Verify the Mosquitto add-on is running
- In HA Developer Tools → MQTT, subscribe to `mbus/#` and check for messages

### `Error querying device` in logs
- Confirm the gateway IP and port are correct
- Verify the M-Bus address matches the device
- Test manually: `mbus-tcp-request-data <host> <port> <address>`

### Entity shows `unavailable`
- The availability topic `mbus/{id}/availability` is set to `offline` when a poll fails
- Check network connectivity to the gateway
- Ensure the device is powered and on the bus

### Sensors have unexpected units
- The unit mapping is based on the exact string in the `<Unit>` field of the M-Bus XML
- Open an issue on the repository with the raw XML output from your device
