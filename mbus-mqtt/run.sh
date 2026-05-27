#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

# ── MQTT service guard ────────────────────────────────────────────────────────
if ! bashio::services.available "mqtt"; then
    bashio::log.fatal "No MQTT service is available."
    bashio::log.fatal "Please install and start the Mosquitto broker add-on."
    bashio::exit.nok
fi

# ── Read add-on options ───────────────────────────────────────────────────────
MBUS_HOST=$(bashio::config 'mbus_host')
MBUS_PORT=$(bashio::config 'mbus_port')
MBUS_ADDRESS=$(bashio::config 'mbus_address')
POLL_INTERVAL=$(bashio::config 'poll_interval')

# Optional fields — default to empty string if not set
DEVICE_NAME=""
DEVICE_AREA=""
if bashio::config.has_value 'device_name'; then
    DEVICE_NAME=$(bashio::config 'device_name')
fi
if bashio::config.has_value 'device_area'; then
    DEVICE_AREA=$(bashio::config 'device_area')
fi

# ── MQTT credentials from Supervisor service discovery ────────────────────────
MQTT_HOST=$(bashio::services mqtt "host")
MQTT_PORT=$(bashio::services mqtt "port")
MQTT_USER=$(bashio::services mqtt "username")
MQTT_PASS=$(bashio::services mqtt "password")
MQTT_SSL=$(bashio::services mqtt "ssl")

# NOTE: MQTT_PASS is intentionally omitted from log lines. Do not add it.
bashio::log.info "M-Bus gateway : ${MBUS_HOST}:${MBUS_PORT}  address=${MBUS_ADDRESS}"
bashio::log.info "Poll interval : ${POLL_INTERVAL}s"
bashio::log.info "MQTT broker   : ${MQTT_HOST}:${MQTT_PORT}  user=${MQTT_USER}  ssl=${MQTT_SSL}"

# ── Environment for Python ────────────────────────────────────────────────────
export MBUS_HOST
export MBUS_PORT
export MBUS_ADDRESS
export POLL_INTERVAL
export DEVICE_NAME
export DEVICE_AREA
export MQTT_HOST
export MQTT_PORT
export MQTT_USER
export MQTT_PASS
export MQTT_SSL
# Note: LD_LIBRARY_PATH for /usr/local/lib is NOT needed on Alpine (musl libc
# searches that path by default). mbus_parser.py strips MQTT_PASS from the
# subprocess environment so it is not inherited by mbus-tcp-request-data.

exec python3 /app/main.py
