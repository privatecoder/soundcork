import sys
import types
from types import SimpleNamespace
from typing import Any, cast


pydantic_settings = types.ModuleType("pydantic_settings")


class BaseSettings:
    pass


def SettingsConfigDict(**kwargs):
    return kwargs


pydantic_settings.BaseSettings = BaseSettings
pydantic_settings.SettingsConfigDict = SettingsConfigDict
soundtouchclient = types.ModuleType("bosesoundtouchapi.soundtouchclient")
soundtouchclient.ContentItem = object
soundtouchclient.SoundTouchClient = object
soundtouchclient.SoundTouchDevice = object
soundtouchdiscovery = types.ModuleType("bosesoundtouchapi.soundtouchdiscovery")
soundtouchdiscovery.SoundTouchDiscovery = object
package = types.ModuleType("bosesoundtouchapi")
package.soundtouchclient = soundtouchclient
package.soundtouchdiscovery = soundtouchdiscovery
sys.modules["pydantic_settings"] = pydantic_settings
sys.modules["bosesoundtouchapi"] = package
sys.modules["bosesoundtouchapi.soundtouchclient"] = soundtouchclient
sys.modules["bosesoundtouchapi.soundtouchdiscovery"] = soundtouchdiscovery

from soundcork.ui.speakers import Speakers


class FakeDiscovery:
    def __init__(self, areDevicesVerified: bool) -> None:
        self.areDevicesVerified = areDevicesVerified
        self.VerifiedDevices = {}
        self.DiscoveredDeviceNames = {}
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
