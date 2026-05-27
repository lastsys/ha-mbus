"""Tests for mbus_parser.py — no hardware or subprocess required."""

import sys
import os
import xml.etree.ElementTree as ET

import pytest

# Add the app directory to the path so we can import the modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from mbus_parser import (  # noqa: E402
    SlaveInfo,
    _coerce_value,
    _parse_xml,
    make_device_id,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(filename: str) -> str:
    with open(os.path.join(FIXTURES, filename), encoding="iso-8859-1") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# SlaveInfo parsing
# ---------------------------------------------------------------------------

class TestSlaveInfo:
    def test_cold_water_slave_info(self):
        slave, _ = _parse_xml(_load("cold_water.xml"))
        assert slave.id == "12345678"
        assert slave.manufacturer == "KAW"
        assert slave.version == "60"
        assert slave.medium == "Cold water"
        assert slave.access_number == 4
        assert slave.status == "00"
        assert slave.signature == "0000"

    def test_heat_meter_slave_info(self):
        slave, _ = _parse_xml(_load("heat_meter.xml"))
        assert slave.id == "12345678"
        assert slave.manufacturer == "KAM"
        assert slave.medium == "Heat (outlet)"
        assert slave.product_name == "Multical 302"

    def test_missing_slave_info_returns_defaults(self):
        xml = "<MBusData></MBusData>"
        slave, records = _parse_xml(xml)
        assert slave.id == ""
        assert slave.manufacturer == ""
        assert records == []


# ---------------------------------------------------------------------------
# DataRecord parsing
# ---------------------------------------------------------------------------

class TestDataRecords:
    def test_cold_water_record_count(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        assert len(records) == 5

    def test_record_ids(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        assert [r.id for r in records] == [0, 1, 2, 3, 4]

    def test_volume_record(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        vol = records[1]
        assert vol.id == 1
        assert vol.unit == "Volume (m m^3)"
        assert vol.value == 7712.0
        assert vol.storage_number == 0
        assert vol.function == "Instantaneous value"

    def test_date_record_remains_string(self):
        """A date value like '2026-05-01' must NOT be coerced to float."""
        _, records = _parse_xml(_load("cold_water.xml"))
        date_rec = records[3]
        assert date_rec.unit == "Time Point (date)"
        assert isinstance(date_rec.value, str)
        assert date_rec.value == "2026-05-01"

    def test_historic_volume_record(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        historic = records[2]
        assert historic.storage_number == 1
        assert historic.value == 0.0

    def test_operating_time_record(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        ot = records[4]
        assert ot.unit == "Operating time (minutes)"
        assert ot.value == 169131.0

    def test_manufacturer_specific_record(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        mfr = records[0]
        assert mfr.unit == "Manufacturer specific"
        assert mfr.value == 16.0

    def test_timestamp_preserved(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        assert records[0].timestamp == "2026-05-27T14:29:35Z"

    def test_tariff_none_when_absent(self):
        _, records = _parse_xml(_load("cold_water.xml"))
        assert all(r.tariff is None for r in records)

    def test_tariff_parsed_when_present(self):
        xml = """<MBusData>
            <SlaveInformation><Id>1</Id><Manufacturer>X</Manufacturer>
            <Version>1</Version><Medium>Heat</Medium></SlaveInformation>
            <DataRecord id="0">
                <Function>Instantaneous value</Function>
                <StorageNumber>0</StorageNumber>
                <Tariff>2</Tariff>
                <Unit>Energy (kWh)</Unit>
                <Value>100</Value>
                <Timestamp>2026-01-01T00:00:00Z</Timestamp>
            </DataRecord>
        </MBusData>"""
        _, records = _parse_xml(xml)
        assert records[0].tariff == 2


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

class TestCoerceValue:
    def test_integer_string(self):
        assert _coerce_value("7712") == 7712.0
        assert isinstance(_coerce_value("7712"), float)

    def test_float_string(self):
        assert _coerce_value("3.14") == pytest.approx(3.14)

    def test_date_string(self):
        val = _coerce_value("2026-05-01")
        assert val == "2026-05-01"
        assert isinstance(val, str)

    def test_zero(self):
        assert _coerce_value("0") == 0.0

    def test_negative(self):
        assert _coerce_value("-5") == -5.0

    def test_empty_string(self):
        assert _coerce_value("") == ""


# ---------------------------------------------------------------------------
# Device ID
# ---------------------------------------------------------------------------

class TestMakeDeviceId:
    def test_normal_id(self):
        slave = SlaveInfo(id="12345678")
        assert make_device_id(slave) == "12345678"

    def test_empty_id_returns_unknown(self):
        slave = SlaveInfo(id="")
        assert make_device_id(slave) == "unknown"

    def test_whitespace_stripped(self):
        slave = SlaveInfo(id="  42  ")
        assert make_device_id(slave) == "42"


# ---------------------------------------------------------------------------
# Malformed XML
# ---------------------------------------------------------------------------

class TestMalformedXml:
    def test_parse_error_propagates(self):
        with pytest.raises(ET.ParseError):
            _parse_xml("this is not xml")

    def test_empty_xml_raises(self):
        with pytest.raises(ET.ParseError):
            _parse_xml("")
