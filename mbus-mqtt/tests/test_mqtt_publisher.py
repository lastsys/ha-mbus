"""Tests for mqtt_publisher.py — no MQTT broker or hardware required."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from mbus_parser import _parse_xml  # noqa: E402
from mqtt_publisher import (  # noqa: E402
    _resolve_unit_info,
    _volume_device_class,
    build_device_registry,
    build_discovery_payloads,
    build_state_payload,
    sanitise_device_id,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(filename: str) -> str:
    with open(os.path.join(FIXTURES, filename), encoding="iso-8859-1") as fh:
        return fh.read()


def _cold_water():
    return _parse_xml(_load("cold_water.xml"))


# ---------------------------------------------------------------------------
# Unit resolution
# ---------------------------------------------------------------------------

class TestResolveUnitInfo:
    def test_volume_litre(self):
        result = _resolve_unit_info("Volume (m m^3)", "Cold water")
        assert result is not None
        dc, unit, sc = result
        assert dc == "water"
        assert unit == "L"
        assert sc == "total_increasing"

    def test_volume_cubic_meter(self):
        dc, unit, _ = _resolve_unit_info("Volume (m^3)", "Cold water")
        assert dc == "water"
        assert unit == "m³"

    def test_volume_gas(self):
        dc, _, _ = _resolve_unit_info("Volume (m^3)", "Gas")
        assert dc == "gas"

    def test_energy_kwh(self):
        dc, unit, sc = _resolve_unit_info("Energy (kWh)", "Heat")
        assert dc == "energy"
        assert unit == "kWh"
        assert sc == "total_increasing"

    def test_energy_wh(self):
        dc, unit, _ = _resolve_unit_info("Energy (Wh)", "Heat")
        assert dc == "energy"
        assert unit == "Wh"

    def test_power_w(self):
        dc, unit, sc = _resolve_unit_info("Power (W)", "Heat")
        assert dc == "power"
        assert unit == "W"
        assert sc == "measurement"

    def test_power_kw(self):
        dc, unit, _ = _resolve_unit_info("Power (kW)", "Heat")
        assert dc == "power"
        assert unit == "kW"

    def test_temperature(self):
        dc, unit, sc = _resolve_unit_info("Flow temperature", "Heat")
        assert dc == "temperature"
        assert unit == "°C"
        assert sc == "measurement"

    def test_operating_time_minutes(self):
        dc, unit, sc = _resolve_unit_info("Operating time (minutes)", "Cold water")
        assert dc == "duration"
        assert unit == "min"
        assert sc == "total_increasing"

    def test_time_point_date(self):
        dc, unit, _ = _resolve_unit_info("Time Point (date)", "Cold water")
        assert dc is None
        assert unit is None

    def test_manufacturer_specific_skipped(self):
        assert _resolve_unit_info("Manufacturer specific", "Cold water") is None

    def test_empty_unit_skipped(self):
        assert _resolve_unit_info("", "Cold water") is None

    def test_unknown_unit_returns_generic(self):
        result = _resolve_unit_info("Something weird", "Electricity")
        assert result is not None
        dc, unit, sc = result
        assert dc is None
        assert unit == "Something weird"
        assert sc == "measurement"

    # ── Volume unit string coverage (libmbus VIF 0x10-0x17) ──────────────

    def test_volume_milli_m3_maps_to_litres(self):
        """VIF 0x13: 10⁻³ m³ = 1 L.  The user's water meter uses this."""
        result = _resolve_unit_info("Volume (m m^3)", "Cold water")
        assert result is not None
        dc, unit, sc = result
        assert dc == "water"
        assert unit == "L"
        assert sc == "total_increasing"

    def test_volume_1e_minus2_maps_to_litres(self):
        """VIF 0x14: 10⁻² m³ = 10 L."""
        result = _resolve_unit_info("Volume (1e-2 m^3)", "Cold water")
        assert result is not None
        dc, unit, _ = result
        assert dc == "water"
        assert unit == "L"

    def test_volume_1e_minus1_maps_to_m3(self):
        """VIF 0x15: 10⁻¹ m³ = 100 L.  libmbus outputs '1e-1', NOT '0.1'."""
        result = _resolve_unit_info("Volume (1e-1 m^3)", "Cold water")
        assert result is not None
        dc, unit, _ = result
        assert dc == "water"
        assert unit == "m³"

    def test_volume_with_space_prefix_maps_to_m3(self):
        """VIF 0x16: 1 m³.  libmbus outputs 'Volume ( m^3)' with a leading space."""
        result = _resolve_unit_info("Volume ( m^3)", "Cold water")
        assert result is not None
        dc, unit, _ = result
        assert dc == "water"
        assert unit == "m³"

    def test_volume_flow_not_shadowed_by_volume_catchall(self):
        """'Volume flow' must NOT be matched by the 'Volume' catch-all entry.

        This verifies the critical ordering invariant: the 'Volume flow' entry
        appears before 'Volume' in _UNIT_MAP so it is matched first.
        """
        result = _resolve_unit_info("Volume flow", "Heat")
        assert result is not None
        dc, unit, sc = result
        # Must be a flow-rate sensor, NOT a volume/total_increasing sensor
        assert dc is None
        assert unit == "m³/h"
        assert sc == "measurement"

    def test_volume_flow_not_classified_as_total_increasing(self):
        """Regression: 'Volume flow' should never get state_class total_increasing."""
        result = _resolve_unit_info("Volume flow", "Cold water")
        assert result is not None
        _, _, sc = result
        assert sc != "total_increasing", (
            "'Volume flow' was matched by the 'Volume' catch-all — check UNIT_MAP ordering"
        )


# ---------------------------------------------------------------------------
# Volume device class inference
# ---------------------------------------------------------------------------

class TestSanitiseDeviceId:
    def test_normal_serial(self):
        assert sanitise_device_id("12345678") == "12345678"

    def test_empty_returns_unknown(self):
        assert sanitise_device_id("") == "unknown"
        assert sanitise_device_id("   ") == "unknown"

    def test_mqtt_wildcards_replaced(self):
        assert sanitise_device_id("meter#1") == "meter_1"
        assert sanitise_device_id("meter+1") == "meter_1"

    def test_slash_replaced(self):
        assert sanitise_device_id("12/34") == "12_34"

    def test_allowed_chars_unchanged(self):
        assert sanitise_device_id("abc-123_XYZ") == "abc-123_XYZ"


class TestVolumeDeviceClass:
    def test_cold_water(self):
        assert _volume_device_class("Cold water") == "water"

    def test_warm_water(self):
        assert _volume_device_class("Warm water") == "water"

    def test_gas(self):
        assert _volume_device_class("Gas") == "gas"

    def test_heat(self):
        assert _volume_device_class("Heat (outlet)") == "water"

    def test_unknown_defaults_to_water(self):
        assert _volume_device_class("Unknown") == "water"


# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------

class TestBuildDeviceRegistry:
    def test_with_custom_name(self):
        slave, _ = _cold_water()
        reg = build_device_registry(slave, "My Meter", "Basement")
        assert reg["name"] == "My Meter"
        assert reg["suggested_area"] == "Basement"
        assert "mbus_12345678" in reg["identifiers"]

    def test_without_custom_name_derives_from_medium(self):
        slave, _ = _cold_water()
        reg = build_device_registry(slave, "", "")
        assert "Cold water" in reg["name"] or "12345678" in reg["name"]

    def test_identifiers_contain_meter_id(self):
        slave, _ = _cold_water()
        reg = build_device_registry(slave, "", "")
        assert any("12345678" in ident for ident in reg["identifiers"])

    def test_manufacturer_included(self):
        slave, _ = _cold_water()
        reg = build_device_registry(slave, "", "")
        assert reg.get("manufacturer") == "KAW"

    def test_no_suggested_area_when_empty(self):
        slave, _ = _cold_water()
        reg = build_device_registry(slave, "", "")
        assert "suggested_area" not in reg


# ---------------------------------------------------------------------------
# Discovery payloads
# ---------------------------------------------------------------------------

class TestBuildDiscoveryPayloads:
    def setup_method(self):
        slave, records = _cold_water()
        self.slave = slave
        self.records = records
        self.device_id = "12345678"
        self.device_reg = build_device_registry(slave, "Cold Water Meter", "")
        self.payloads = build_discovery_payloads(
            records, slave, self.device_id, self.device_reg
        )

    def test_manufacturer_specific_excluded(self):
        """Record 0 (Manufacturer specific) must not produce a discovery entry."""
        topics = [t for t, _ in self.payloads]
        assert not any("_0/" in t for t in topics)

    def test_volume_record_included(self):
        """Record 1 (Volume) must produce a discovery entry."""
        topics = [t for t, _ in self.payloads]
        assert any("_1/" in t for t in topics)

    def test_topic_format(self):
        for topic, _ in self.payloads:
            assert topic.startswith("homeassistant/sensor/mbus_12345678_")
            assert topic.endswith("/config")

    def test_volume_payload_fields(self):
        _, payload = next(
            (t, p) for t, p in self.payloads if "_1/" in t
        )
        assert payload["device_class"] == "water"
        assert payload["unit_of_measurement"] == "L"
        assert payload["state_class"] == "total_increasing"
        assert payload["unique_id"] == "mbus_12345678_1"
        assert "value_json.records['1'].value" in payload["value_template"]

    def test_availability_topic_present(self):
        for _, payload in self.payloads:
            assert payload["availability_topic"] == "mbus/12345678/availability"
            assert payload["payload_available"] == "online"
            assert payload["payload_not_available"] == "offline"

    def test_device_block_present(self):
        for _, payload in self.payloads:
            assert "device" in payload
            assert payload["device"]["name"] == "Cold Water Meter"

    def test_state_topic_correct(self):
        for _, payload in self.payloads:
            assert payload["state_topic"] == "mbus/12345678/state"

    def test_operating_time_payload(self):
        _, payload = next(
            (t, p) for t, p in self.payloads if "_4/" in t
        )
        assert payload["device_class"] == "duration"
        assert payload["unit_of_measurement"] == "min"

    def test_date_record_has_no_device_class(self):
        """Time Point (date) should produce a sensor without a device_class."""
        _, payload = next(
            (t, p) for t, p in self.payloads if "_3/" in t
        )
        assert "device_class" not in payload


# ---------------------------------------------------------------------------
# State payload
# ---------------------------------------------------------------------------

class TestBuildStatePayload:
    def setup_method(self):
        self.slave, self.records = _cold_water()

    def test_top_level_fields(self):
        payload = build_state_payload(self.slave, self.records)
        assert payload["device_id"] == "12345678"
        assert payload["manufacturer"] == "KAW"
        assert payload["medium"] == "Cold water"
        assert "records" in payload
        assert "timestamp" in payload

    def test_records_keyed_by_string_id(self):
        payload = build_state_payload(self.slave, self.records)
        assert "0" in payload["records"]
        assert "1" in payload["records"]
        assert "4" in payload["records"]

    def test_volume_value(self):
        payload = build_state_payload(self.slave, self.records)
        assert payload["records"]["1"]["value"] == 7712.0
        assert payload["records"]["1"]["unit"] == "Volume (m m^3)"

    def test_date_value_is_string(self):
        payload = build_state_payload(self.slave, self.records)
        assert payload["records"]["3"]["value"] == "2026-05-01"
        assert isinstance(payload["records"]["3"]["value"], str)

    def test_storage_number_included(self):
        payload = build_state_payload(self.slave, self.records)
        assert payload["records"]["2"]["storage"] == 1

    def test_serialisable_to_json(self):
        payload = build_state_payload(self.slave, self.records)
        dumped = json.dumps(payload)
        loaded = json.loads(dumped)
        assert loaded["device_id"] == "12345678"
