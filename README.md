# M-Bus MQTT — Home Assistant Add-on

A Home Assistant add-on that reads a single M-Bus device over TCP (using [libmbus](https://github.com/rscada/libmbus)) and publishes sensor data to MQTT with full Home Assistant auto-discovery.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- Polls any M-Bus device reachable via a TCP gateway (e.g. Relay/MBUS-LAN adapters, DSMR gateways)
- Parses all `<DataRecord>` entries from the M-Bus XML response
- Maps M-Bus units to Home Assistant device classes (water, energy, power, temperature, …)
- Publishes MQTT auto-discovery messages so sensors appear automatically in the HA UI
- Tracks availability: sets entities to `unavailable` if the device stops responding
- Automatically detects MQTT broker credentials from the Mosquitto add-on via HA Supervisor

## Requirements

- Home Assistant OS or Supervised installation
- [Mosquitto broker add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) installed and running
- An M-Bus device reachable via a TCP gateway

## Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**
2. Click the ⋮ menu (top right) → **Repositories**
3. Add the repository URL:
   ```
   https://git.int.lastsys.com/stefan/ha-mbus
   ```
4. Find **M-Bus MQTT** in the add-on store and click **Install**

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mbus_host` | string | *(required)* | Hostname or IP of the M-Bus TCP gateway |
| `mbus_port` | integer | `10001` | TCP port of the gateway |
| `mbus_address` | integer | `1` | Primary M-Bus device address (0–250) |
| `poll_interval` | integer | `60` | Seconds between polls |
| `device_name` | string | *(optional)* | Friendly name shown in HA |
| `device_area` | string | *(optional)* | HA area (e.g. `Basement`) |

**Example:**
```yaml
mbus_host: "192.168.1.100"
mbus_port: 10001
mbus_address: 66
poll_interval: 300
device_name: "Cold Water Meter"
device_area: "Utility Room"
```

## MQTT Topics

| Topic | Description |
|-------|-------------|
| `mbus/{meter_id}/state` | JSON payload with all data records (retained) |
| `mbus/{meter_id}/availability` | `online` / `offline` (LWT) |
| `homeassistant/sensor/mbus_{meter_id}_{record_id}/config` | Auto-discovery config (retained) |

## Supported Meter Types

The add-on handles any M-Bus device. Unit-to-device-class mappings are built in for:

- **Water meters** — Volume → `water` device class
- **Heat/cooling meters** — Energy → `energy`, Power → `power`, Temperature → `temperature`
- **Gas meters** — Volume → `gas` device class
- **Electricity meters** — Energy → `energy`, Power → `power`, Current/Voltage
- **Generic** — Operating time, pressure, flow rate, and more

## Development

See [CLAUDE.md](CLAUDE.md) for developer notes, local test commands, and build instructions.

## License

MIT
