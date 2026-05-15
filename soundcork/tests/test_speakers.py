from types import SimpleNamespace
from typing import Any, cast

from soundcork.ui.speakers import Speakers


class FakeDiscovery:
    def __init__(self, areDevicesVerified: bool) -> None:
        self.areDevicesVerified = areDevicesVerified
        self.VerifiedDevices: dict[str, object] = {}
        self.DiscoveredDeviceNames: dict[str, str] = {}
        self.timeouts: list[int] = []

    def DiscoverDevices(self, timeout: int) -> None:
        self.timeouts.append(timeout)


class FakeDatastore:
    def list_accounts(self) -> list[str]:
        return []


def test_speakers_use_longer_discovery_timeout(monkeypatch):
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDiscovery", FakeDiscovery)

    speakers = Speakers(
        cast(Any, FakeDatastore()),
        cast(Any, SimpleNamespace(base_url="http://soundcork.local")),
    )

    assert speakers._st_discovery.timeouts == [5]
