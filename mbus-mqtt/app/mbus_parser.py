"""mbus_parser.py — Run mbus-tcp-request-data and parse the XML response.

The libmbus tool outputs XML with encoding="ISO-8859-1".  We must capture raw
bytes and decode manually — do NOT pass text=True to subprocess.run().

Security notes
--------------
* The subprocess is called with a stripped environment that excludes MQTT_PASS,
  so the broker password is not inherited by the mbus-tcp-request-data process.
* XML is parsed with defusedxml to guard against entity-expansion (billion-laughs)
  attacks from a compromised or malicious M-Bus gateway.

Raises on the caller:
  subprocess.CalledProcessError  — non-zero exit from mbus-tcp-request-data
  subprocess.TimeoutExpired       — device did not respond within timeout
  defusedxml.ElementTree.ParseError — malformed or hostile XML in the response
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Union

try:
    import defusedxml.ElementTree as ET  # preferred: guards against billion-laughs XML attacks
except ImportError:  # dev environment without defusedxml installed
    import xml.etree.ElementTree as ET  # type: ignore[no-redef]
from xml.etree.ElementTree import Element as _Element


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SlaveInfo:
    """Metadata from the <SlaveInformation> block."""
    id: str = ""
    manufacturer: str = ""
    version: str = ""
    product_name: str = ""
    medium: str = ""
    access_number: int = 0
    status: str = ""
    signature: str = ""


@dataclass
class DataRecord:
    """One <DataRecord> entry from the M-Bus response."""
    id: int = 0
    function: str = ""
    storage_number: int = 0
    tariff: int | None = None
    device: int | None = None
    unit: str = ""
    # Value can be numeric or a string (e.g. dates like "2026-05-01")
    value: Union[float, str] = 0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_device(
    host: str,
    port: int,
    address: int,
    timeout: int = 30,
) -> tuple[SlaveInfo, list[DataRecord]]:
    """Query the M-Bus device and return structured data.

    Runs ``mbus-tcp-request-data <host> <port> <address>`` and parses the
    XML output into a ``SlaveInfo`` and a list of ``DataRecord`` objects.

    Args:
        host:     IP/hostname of the TCP gateway.
        port:     TCP port.
        address:  Primary M-Bus address (0–250).
        timeout:  Subprocess timeout in seconds.

    Returns:
        A ``(SlaveInfo, [DataRecord, …])`` tuple.

    Raises:
        subprocess.CalledProcessError:   Non-zero exit (device error / wrong address).
        subprocess.TimeoutExpired:        No response within *timeout* seconds.
        defusedxml.ElementTree.ParseError: Response is not valid XML.
    """
    # Strip secrets from the subprocess environment.  The mbus tool has no
    # need for MQTT_PASS or other credentials, and passing them risks exposure
    # via /proc/<pid>/environ while the process runs.
    safe_env = {k: v for k, v in os.environ.items() if k not in ("MQTT_PASS",)}

    result = subprocess.run(
        ["mbus-tcp-request-data", host, str(port), str(address)],
        capture_output=True,
        timeout=timeout,
        check=True,         # raises CalledProcessError on non-zero exit
        env=safe_env,
    )
    # libmbus declares ISO-8859-1 in the XML prolog — decode accordingly.
    xml_text = result.stdout.decode("iso-8859-1")
    return _parse_xml(xml_text)


def make_device_id(slave_info: SlaveInfo) -> str:
    """Return the raw meter serial from ``SlaveInfo.id``.

    Returns the stripped serial number string, or ``"unknown"`` if the device
    did not report one.  The caller is responsible for sanitising this value
    before embedding it in MQTT topic strings (see
    ``mqtt_publisher.sanitise_device_id``).
    """
    raw = slave_info.id.strip()
    return raw if raw else "unknown"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_xml(xml_text: str) -> tuple[SlaveInfo, list[DataRecord]]:
    """Parse the MBusData XML string into dataclasses.

    Uses defusedxml to prevent entity-expansion attacks.
    """
    root = ET.fromstring(xml_text)

    slave_info = _parse_slave_info(root.find("SlaveInformation"))
    records: list[DataRecord] = []
    for elem in root.findall("DataRecord"):
        records.append(_parse_data_record(elem))

    return slave_info, records


def _parse_slave_info(elem: _Element | None) -> SlaveInfo:
    if elem is None:
        return SlaveInfo()
    return SlaveInfo(
        id=_text(elem, "Id"),
        manufacturer=_text(elem, "Manufacturer"),
        version=_text(elem, "Version"),
        product_name=_text(elem, "ProductName"),
        medium=_text(elem, "Medium"),
        access_number=_int(elem, "AccessNumber"),
        status=_text(elem, "Status"),
        signature=_text(elem, "Signature"),
    )


def _parse_data_record(elem: _Element) -> DataRecord:
    record_id = int(elem.get("id", "0"))
    return DataRecord(
        id=record_id,
        function=_text(elem, "Function"),
        storage_number=_int(elem, "StorageNumber"),
        tariff=_optional_int(elem.findtext("Tariff")),
        device=_optional_int(elem.findtext("Device")),
        unit=_text(elem, "Unit"),
        value=_coerce_value(_text(elem, "Value")),
        timestamp=_text(elem, "Timestamp"),
    )


def _text(elem: _Element, tag: str) -> str:
    """Return stripped text of a child element, or empty string."""
    child = elem.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _int(elem: _Element, tag: str) -> int:
    """Return integer value of a child element, or 0."""
    try:
        return int(_text(elem, tag))
    except (ValueError, TypeError):
        return 0


def _optional_int(text: str | None) -> int | None:
    """Convert *text* to int, or return None if absent or non-numeric."""
    if text is None:
        return None
    try:
        return int(text.strip())
    except (ValueError, TypeError):
        return None


def _coerce_value(raw: str) -> Union[float, str]:
    """Try to convert *raw* to float; return original string on failure."""
    try:
        return float(raw)
    except (ValueError, TypeError):
        return raw
