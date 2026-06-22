"""Unit tests for device network helpers."""

from soundcork import devices


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
