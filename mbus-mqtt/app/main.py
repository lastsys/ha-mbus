"""main.py — Entry point for the M-Bus MQTT bridge.

Reads configuration from environment variables (set by run.sh from the HA
Supervisor options), connects to MQTT, and runs a poll loop that:

  1. Pre-polls the M-Bus device synchronously (before connecting to MQTT) so
     we know the meter serial number when setting the Last-Will Testament.
  2. Connects to the MQTT broker with the correct LWT topic.
  3. Publishes HA auto-discovery configs (on first poll and on every reconnect).
  4. Publishes a JSON state payload with all DataRecord values.
  5. Publishes ``online`` to the availability topic.
  6. Sleeps for ``poll_interval`` seconds and repeats.

On device error (subprocess failure / timeout / XML parse error), the error is
logged with detail, ``offline`` is published to the availability topic, and the
loop continues with the next poll cycle.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import xml.etree.ElementTree as _ET  # only for ParseError type in except clause
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from mbus_parser import make_device_id, read_device
from mqtt_publisher import (
    build_device_registry,
    build_discovery_payloads,
    publish_discovery,
    publish_state,
    sanitise_device_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger("mbus_mqtt")

# defusedxml exceptions all subclass xml.etree.ElementTree.ParseError so we
# can catch _ET.ParseError regardless of which backend was imported in mbus_parser.
_ET_PARSE_ERROR = _ET.ParseError


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    mbus_host: str
    mbus_port: int
    mbus_address: int
    poll_interval: int
    device_name: str
    device_area: str
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_pass: str
    mqtt_ssl: bool

    @classmethod
    def from_env(cls) -> "Config":
        def _require(key: str) -> str:
            val = os.environ.get(key, "").strip()
            if not val:
                _LOGGER.error("Required environment variable %s is not set", key)
                sys.exit(1)
            return val

        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "").strip()
            try:
                return int(raw) if raw else default
            except ValueError:
                _LOGGER.warning(
                    "Invalid integer for %s=%r, using default %d", key, raw, default
                )
                return default

        def _bool(key: str, default: bool = False) -> bool:
            raw = os.environ.get(key, "").strip().lower()
            return raw in ("true", "1", "yes") if raw else default

        return cls(
            mbus_host=_require("MBUS_HOST"),
            mbus_port=_int("MBUS_PORT", 10001),
            mbus_address=_int("MBUS_ADDRESS", 1),
            poll_interval=_int("POLL_INTERVAL", 60),
            device_name=os.environ.get("DEVICE_NAME", "").strip(),
            device_area=os.environ.get("DEVICE_AREA", "").strip(),
            mqtt_host=_require("MQTT_HOST"),
            mqtt_port=_int("MQTT_PORT", 1883),
            mqtt_user=os.environ.get("MQTT_USER", "").strip(),
            mqtt_pass=os.environ.get("MQTT_PASS", "").strip(),
            mqtt_ssl=_bool("MQTT_SSL"),
        )


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class MBusMQTTBridge:
    """Connects to MQTT, polls the M-Bus device, and publishes data."""

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._device_id: str = ""
        # Protects _discovery_payloads which is written on the poll thread
        # and read on the paho network thread (on_connect callback).
        self._discovery_lock = threading.Lock()
        self._discovery_payloads: list[tuple[str, dict]] = []
        self._stop_event = threading.Event()

        self._client = mqtt.Client(client_id="mbus_mqtt_addon", clean_session=True)

    # ── MQTT callbacks ────────────────────────────────────────────────────

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: object,
        _flags: dict,
        rc: int,
    ) -> None:
        if rc != 0:
            _LOGGER.error("MQTT connection failed: rc=%d", rc)
            return
        _LOGGER.info("MQTT connected to %s:%d", self._cfg.mqtt_host, self._cfg.mqtt_port)
        # Re-publish discovery on every (re)connect so HA picks up entities
        # after a broker restart.  Use a copy under the lock to avoid races.
        with self._discovery_lock:
            payloads = list(self._discovery_payloads)
        if payloads:
            _LOGGER.info("Re-publishing %d discovery config(s)", len(payloads))
            publish_discovery(client, payloads)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: object,
        rc: int,
    ) -> None:
        if rc != 0:
            _LOGGER.warning(
                "MQTT disconnected unexpectedly (rc=%d); paho will reconnect", rc
            )

    # ── Setup ─────────────────────────────────────────────────────────────

    def _pre_poll(self) -> None:
        """Poll the device once, before connecting to MQTT, to determine its
        serial number.  The serial is used as the MQTT LWT topic, which must
        be set *before* ``client.connect()``.

        Retries up to 5 times with 10-second back-off.  Falls back to the
        string ``"unknown"`` so startup is not blocked indefinitely.
        """
        cfg = self._cfg
        for attempt in range(1, 6):
            try:
                slave_info, _ = read_device(
                    cfg.mbus_host, cfg.mbus_port, cfg.mbus_address, timeout=30
                )
                raw_id = make_device_id(slave_info)
                self._device_id = sanitise_device_id(raw_id)
                _LOGGER.info(
                    "Pre-poll: meter serial=%s  manufacturer=%s  medium=%s",
                    slave_info.id, slave_info.manufacturer, slave_info.medium,
                )
                return
            except Exception as exc:
                _LOGGER.warning(
                    "Pre-poll attempt %d/5 failed: %s", attempt, exc
                )
                if attempt < 5:
                    self._stop_event.wait(timeout=10)

        _LOGGER.warning(
            "Could not determine meter serial after 5 attempts; "
            "using 'unknown' as device ID"
        )
        self._device_id = "unknown"

    def _connect_mqtt(self) -> None:
        """Connect to the MQTT broker.

        Must be called *after* ``_pre_poll()`` so that ``self._device_id`` is
        set and the LWT topic is correct.
        """
        cfg = self._cfg
        if cfg.mqtt_user:
            self._client.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass)
        if cfg.mqtt_ssl:
            self._client.tls_set()

        # LWT: broker publishes "offline" if the client disconnects unexpectedly.
        # self._device_id is now known from the pre-poll.
        lwt_topic = f"mbus/{self._device_id}/availability"
        self._client.will_set(lwt_topic, payload="offline", retain=True, qos=1)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        _LOGGER.info(
            "Connecting to MQTT broker %s:%d (LWT → %s) …",
            cfg.mqtt_host, cfg.mqtt_port, lwt_topic,
        )
        self._client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
        self._client.loop_start()

    def _set_availability(self, online: bool) -> None:
        if not self._device_id:
            return
        topic = f"mbus/{self._device_id}/availability"
        payload = "online" if online else "offline"
        self._client.publish(topic, payload, retain=True, qos=1)

    # ── Poll ──────────────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        cfg = self._cfg
        _LOGGER.info(
            "Polling %s:%d address=%d",
            cfg.mbus_host, cfg.mbus_port, cfg.mbus_address,
        )
        try:
            slave_info, records = read_device(
                cfg.mbus_host, cfg.mbus_port, cfg.mbus_address, timeout=30
            )
        except subprocess.TimeoutExpired:
            _LOGGER.warning(
                "M-Bus read timed out after 30 s (host=%s port=%d address=%d)",
                cfg.mbus_host, cfg.mbus_port, cfg.mbus_address,
            )
            self._set_availability(False)
            return
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("iso-8859-1", errors="replace").strip()
            _LOGGER.warning(
                "mbus-tcp-request-data exited with rc=%d: %s",
                exc.returncode, stderr or "(no stderr)",
            )
            self._set_availability(False)
            return
        except _ET_PARSE_ERROR as exc:
            _LOGGER.error("XML parse error from M-Bus device: %s", exc)
            self._set_availability(False)
            return
        except Exception:
            _LOGGER.exception("Unexpected error during M-Bus poll")
            self._set_availability(False)
            return

        raw_id = make_device_id(slave_info)
        device_id = sanitise_device_id(raw_id)

        if device_id != self._device_id:
            if self._device_id:
                # Retire the old device: mark its availability topic offline
                # so HA doesn't keep showing a ghost device as available.
                _LOGGER.info(
                    "Device ID changed %s → %s; retiring old topics",
                    self._device_id, device_id,
                )
                self._client.publish(
                    f"mbus/{self._device_id}/availability",
                    payload="offline", retain=True, qos=1,
                )
            self._device_id = device_id
            _LOGGER.info(
                "Device identified: id=%s  manufacturer=%s  medium=%s",
                slave_info.id, slave_info.manufacturer, slave_info.medium,
            )

        device_reg = build_device_registry(
            slave_info, cfg.device_name, cfg.device_area
        )
        new_payloads = build_discovery_payloads(
            records, slave_info, device_id, device_reg
        )

        # Check whether we need to (re-)publish discovery.
        # We publish on first contact and whenever the payload list changes
        # (e.g. different number of records after a firmware update).
        with self._discovery_lock:
            needs_publish = new_payloads != self._discovery_payloads
            if needs_publish:
                self._discovery_payloads = new_payloads

        if needs_publish:
            _LOGGER.info(
                "Publishing %d discovery config(s) for device %s",
                len(new_payloads), device_id,
            )
            publish_discovery(self._client, new_payloads)

        publish_state(self._client, device_id, slave_info, records)
        self._set_availability(True)
        _LOGGER.info(
            "State published for %s (%d record(s))", device_id, len(records)
        )

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Pre-poll, connect to MQTT, then enter the polling loop."""
        # Step 1: get device serial before connecting so LWT topic is correct.
        self._pre_poll()

        # Step 2: connect MQTT (now that device_id is known).
        self._connect_mqtt()

        # Brief pause to let the MQTT connection establish.
        self._stop_event.wait(timeout=2)

        while not self._stop_event.is_set():
            self._poll_once()
            # Sleep interruptibly so SIGTERM wakes us immediately.
            self._stop_event.wait(timeout=self._cfg.poll_interval)

        _LOGGER.info("Shutting down")
        self._set_availability(False)
        self._client.loop_stop()
        self._client.disconnect()

    def stop(self) -> None:
        """Signal the polling loop to exit cleanly."""
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _handle_signal(_signum: int, _frame: object) -> None:
    # Do NOT call logging functions here — logging acquires a lock and calling
    # it from a signal handler can deadlock if the main thread holds that lock.
    # Simply set the stop event; the main loop will log and exit cleanly.
    if "_bridge" in globals():
        _bridge.stop()  # type: ignore[name-defined]


if __name__ == "__main__":
    config = Config.from_env()
    _bridge = MBusMQTTBridge(config)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _bridge.run()
