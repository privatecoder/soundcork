import sys
import types
from pathlib import Path
from typing import Any, cast

from fastapi import Response
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
devices = types.ModuleType("soundcork.devices")
devices.add_device_by_ip = lambda *_args, **_kwargs: True
devices.addr_is_reachable = lambda *_args, **_kwargs: False
devices.override_speaker_config = lambda *_args, **_kwargs: True
devices.reboot_speaker = lambda *_args, **_kwargs: True
package = types.ModuleType("bosesoundtouchapi")
package.soundtouchclient = soundtouchclient
package.soundtouchdiscovery = soundtouchdiscovery
sys.modules["pydantic_settings"] = pydantic_settings
sys.modules["bosesoundtouchapi"] = package
sys.modules["bosesoundtouchapi.soundtouchclient"] = soundtouchclient
sys.modules["bosesoundtouchapi.soundtouchdiscovery"] = soundtouchdiscovery
sys.modules["soundcork.devices"] = devices

from soundcork.admin import get_admin_router


class FakeDatastore:
    def list_accounts(self) -> list[str]:
        return []


class FakeSpeakers:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.forced_refresh_calls = 0

    def refresh_discovery(self, force: bool = False) -> bool:
        self.refresh_calls += 1
        if force:
            self.forced_refresh_calls += 1
        return True

    def all_devices(self) -> dict[str, Any]:
        return {}


class FakeSettings:
    base_url = "http://192.168.1.50:8000"


class FakeTemplates:
    def __init__(self, directory: str) -> None:
        self.directory = directory

    def TemplateResponse(self, *args, **kwargs) -> Response:
        return Response("ok", media_type="text/html")


def test_admin_renders_without_refreshing(monkeypatch):
    """The shell page should not trigger discovery; only the fragment does."""
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("fastapi.templating.Jinja2Templates", FakeTemplates)
    app = FastAPI()
    speakers = FakeSpeakers()
    app.include_router(
        get_admin_router(
            cast(Any, FakeDatastore()),
            cast(Any, speakers),
            cast(Any, FakeSettings()),
        )
    )

    client = TestClient(app)
    response = client.get("/admin/")

    assert response.status_code == 200
    assert speakers.refresh_calls == 0


def test_admin_fragment_forces_discovery(monkeypatch):
    """The fragment endpoint forces a fresh UPnP scan when ?force=true."""
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("fastapi.templating.Jinja2Templates", FakeTemplates)
    app = FastAPI()
    speakers = FakeSpeakers()
    app.include_router(
        get_admin_router(
            cast(Any, FakeDatastore()),
            cast(Any, speakers),
            cast(Any, FakeSettings()),
        )
    )

    client = TestClient(app)
    response = client.get("/admin/devices-fragment?force=true")

    assert response.status_code == 200
    assert speakers.forced_refresh_calls == 1
