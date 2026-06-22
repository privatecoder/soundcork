"""Unit tests for device network helpers."""

import xml.etree.ElementTree as ET

from soundcork import devices
from soundcork.datastore import DataStore
from soundcork.model import ConfiguredSource


def test_read_sources_returns_none_on_ssh_failure(monkeypatch):
    monkeypatch.setattr(devices, "read_file_from_speaker_ssh", lambda **_kwargs: False)

    result = devices.read_sources("192.168.1.10")

    assert result is None


def test_read_sources_returns_contents_on_success(monkeypatch):
    monkeypatch.setattr(devices, "read_file_from_speaker_ssh", lambda **_kwargs: True)

    result = devices.read_sources("192.168.1.10")

    # Successful read of an (empty, because the fake didn't write) temp file is
    # the empty string — crucially NOT None, so callers proceed.
    assert result == ""


def test_default_sources_is_valid_sources_xml():
    """default_sources() seeds a blank account, so it must be a well-formed
    <sources> document with at least one playable local source."""
    xml = devices.default_sources()
    root = ET.fromstring(xml)

    assert root.tag == "sources"
    source_elems = root.findall("source")
    assert source_elems, "default_sources must define at least one source"
    # Every source must carry the attributes the speaker / datastore parser read.
    for elem in source_elems:
        assert elem.attrib.get("displayName")
        assert elem.find("sourceKey") is not None
        assert elem.find("createdOn") is not None
        assert elem.find("updatedOn") is not None


def test_default_sources_round_trips_through_configured_sources(monkeypatch):
    """The seeded Sources.xml must parse back into ConfiguredSource objects via
    the same DataStore.get_configured_sources() path the speaker is served from."""
    xml = devices.default_sources()

    monkeypatch.setattr("soundcork.datastore.settings.data_dir", "/virtual/data")
    monkeypatch.setattr("soundcork.datastore.path.exists", lambda _: True)
    datastore = DataStore()
    monkeypatch.setattr(
        "soundcork.datastore.ET.parse",
        lambda _path: ET.ElementTree(ET.fromstring(xml)),
    )

    sources = datastore.get_configured_sources("7679292")

    assert sources, "round-trip produced no ConfiguredSource objects"
    for source in sources:
        assert isinstance(source, ConfiguredSource)
        assert source.display_name
        assert source.source_key_type
        assert source.id
