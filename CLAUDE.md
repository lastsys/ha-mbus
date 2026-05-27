# Developer Notes — ha-mbus

## Project Overview
Home Assistant add-on: reads a single M-Bus device over TCP via `mbus-tcp-request-data` (libmbus),
parses the XML output, and publishes to MQTT with HA auto-discovery.

## Repository Layout
- `mbus-mqtt/` — the HA add-on (Dockerfile, config.yaml, Python app)
- `repository.yaml` — makes this a valid HA custom add-on repository

## Local Development

### Test mbus-tcp-request-data directly
```bash
mbus-tcp-request-data 192.168.1.100 10001 66
```

### Run the parser on captured XML
```bash
cd mbus-mqtt
python3 -c "
from app.mbus_parser import _parse_xml
xml = open('tests/fixtures/cold_water.xml').read()
slave, records = _parse_xml(xml)
print(slave)
for r in records:
    print(r)
"
```

### Build and test the Docker image (amd64)
```bash
docker build \
  --build-arg BUILD_FROM=ghcr.io/hassio-addons/base:latest \
  -t mbus-mqtt-local \
  ./mbus-mqtt/

# Verify libmbus binary is present
docker run --rm mbus-mqtt-local mbus-tcp-request-data
# Expected: "usage: mbus-tcp-request-data [-d] host port mbus-address"

# Verify Python dependency
docker run --rm mbus-mqtt-local python3 -c "import paho.mqtt.client; print('OK')"
```

### Test with a local MQTT broker
```bash
# Start Mosquitto in Docker
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto \
  mosquitto -c /mosquitto-no-auth.conf

# Subscribe to all topics
mosquitto_sub -h localhost -t '#' -v

# Run the Python app locally (set env vars manually)
cd mbus-mqtt
MBUS_HOST=192.168.1.100 MBUS_PORT=10001 MBUS_ADDRESS=66 \
POLL_INTERVAL=60 DEVICE_NAME="Test Meter" DEVICE_AREA="" \
MQTT_HOST=localhost MQTT_PORT=1883 MQTT_USER="" MQTT_PASS="" \
LD_LIBRARY_PATH=/usr/local/lib \
python3 app/main.py
```

### Run unit tests
```bash
cd mbus-mqtt
pip install pytest paho-mqtt
pytest tests/ -v
```

## Multi-arch Builds

GitHub Actions CI builds all 5 arches using `docker buildx` with QEMU emulation.
For local development, only `amd64` is needed.

## Key Implementation Notes

### ISO-8859-1 XML encoding
libmbus outputs XML with `<?xml version="1.0" encoding="ISO-8859-1"?>`.
The subprocess call must capture raw bytes and decode with `iso-8859-1`:
```python
result = subprocess.run([...], capture_output=True, check=True)
xml_text = result.stdout.decode('iso-8859-1')
```
Do NOT use `text=True` on subprocess.run() — it defaults to the system locale.

### libmbus shared library path
The compiled `libmbus.so` lands in `/usr/local/lib/`.
Alpine's `ldconfig` does not pick this up automatically, so `run.sh` exports:
```bash
export LD_LIBRARY_PATH=/usr/local/lib
```

### bashio service discovery
MQTT credentials come from the HA Supervisor:
```bash
MQTT_HOST=$(bashio::services mqtt "host")
MQTT_USER=$(bashio::services mqtt "username")
MQTT_PASS=$(bashio::services mqtt "password")
```
This requires `services: [mqtt:need]` in `config.yaml`.

### MQTT auto-discovery
Discovery payloads are published with `retain=True` to:
  `homeassistant/sensor/mbus_{device_id}_{record_id}/config`

They are re-published in the `on_connect` callback so HA picks them up
even after a broker restart.

### Unit mapping
`mqtt_publisher.py` contains `UNIT_MAP` — an ordered list of
`(unit_substring, device_class, ha_unit, state_class)` tuples.
Volume unit device_class (`water` vs `gas`) is determined from
`SlaveInfo.medium` at runtime.
