"""Unit tests for marge API helpers."""

import xml.etree.ElementTree as ET

import pytest

from soundcork.marge import add_device_to_account, recents_xml
from soundcork.model import ConfiguredSource, DeviceInfo, Recent


def _device(device_id: str, name: str = "Old Name") -> DeviceInfo:
    return DeviceInfo(
        device_id=device_id,
        product_code="SoundTouch 10",
        device_serial_number="1",
        product_serial_number="2",
        firmware_version="3",
        ip_address="10.0.0.1",
        name=name,
        created_on="",
        updated_on="",
    )


class _MoveDatastore:
    """Records the order of add/remove so we can assert add-before-remove."""

    def __init__(
        self,
        existing: DeviceInfo | None,
        current_account: str | None,
        add_returns_none: bool = False,
    ):
        self._existing = existing
        self._current_account = current_account
        self._add_returns_none = add_returns_none
        self.calls: list[tuple] = []

    def find_device(self, device_id):
        return self._existing, self._current_account

    def add_device(self, account, device_id, device):
        self.calls.append(("add", account, device.name))
        return None if self._add_returns_none else device

    def remove_device(self, account, device_id):
        self.calls.append(("remove", account))
        return True

    def save_device_info(self, device, account):
        self.calls.append(("save", account, device.name))
        return device


def test_add_device_to_account_adds_before_removing_on_move():
    ds = _MoveDatastore(_device("ABC"), current_account="111")
    xml = '<device deviceid="ABC"><name>New Name</name></device>'

    device_id, _elem = add_device_to_account(ds, "222", xml)

    assert device_id == "ABC"
    # The add to the new account must happen before the remove from the old.
    assert ds.calls == [("add", "222", "New Name"), ("remove", "111")]


def test_add_device_to_account_updates_target_when_add_is_noop():
    # add_device() returns None when the target account already has the device;
    # the move must still update the target row (so the rename isn't lost)
    # before removing the old account's row.
    ds = _MoveDatastore(_device("ABC"), current_account="111", add_returns_none=True)
    xml = '<device deviceid="ABC"><name>New Name</name></device>'

    add_device_to_account(ds, "222", xml)

    assert ds.calls == [
        ("add", "222", "New Name"),
        ("save", "222", "New Name"),
        ("remove", "111"),
    ]


def test_add_device_to_account_same_account_updates_in_place():
    ds = _MoveDatastore(_device("ABC"), current_account="222")
    xml = '<device deviceid="ABC"><name>New Name</name></device>'

    add_device_to_account(ds, "222", xml)

    # No destructive remove when the device is already in the target account.
    assert ds.calls == [("save", "222", "New Name")]


class _RecentsDatastore:
    def __init__(self, recents):
        self._recents = recents

    def get_configured_sources(self, account, device=""):
        return [
            ConfiguredSource(
                display_name="Internet Radio",
                id="100006",
                secret="",
                secret_type="",
                source_key_type="INTERNET_RADIO",
                source_key_account="",
                created_on="2024-01-01T00:00:00+00:00",
                updated_on="2024-01-01T00:00:00+00:00",
            )
        ]

    def get_recents(self, account, device):
        return self._recents


def _recent(utc_time: str) -> Recent:
    return Recent(
        id="1",
        name="A",
        source="INTERNET_RADIO",
        type="stationurl",
        location="/v1/playback/station/s1",
        source_account="",
        is_presetable="true",
        device_id="ABC",
        utc_time=utc_time,
        container_art="",
    )


def test_recents_xml_tolerates_bad_utc_time():
    # Regression: an empty/non-numeric utcTime used to raise ValueError and
    # take down the whole account_full_xml response.
    ds = _RecentsDatastore([_recent("")])

    elem = recents_xml(ds, "222", "ABC")

    recent = elem.find("recent")
    assert recent is not None
    # lastplayedat falls back to the default date string rather than raising.
    assert recent.find("lastplayedat").text


def test_recents_xml_uses_numeric_utc_time():
    ds = _RecentsDatastore([_recent("1709640000")])

    elem = recents_xml(ds, "222", "ABC")

    recent = elem.find("recent")
    assert recent is not None
    assert recent.find("lastplayedat").text.startswith("2024-")
