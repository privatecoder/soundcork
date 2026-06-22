from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from soundcork.miniapp import get_miniapp_router
from soundcork.model import Preset

ACCOUNT_ID = "7679292"
DATASTORE_DEVICE_ID = "device-1"
DISCOVERED_ONLY_DEVICE_ID = "C8DF84AC5AB1"


class FakeDatastore:
    def account_exists(self, account_id: str) -> bool:
        return account_id == ACCOUNT_ID

    def list_accounts(self) -> list[str]:
        return [ACCOUNT_ID]

    def get_account_info(self, account_id: str) -> str:
        assert account_id == ACCOUNT_ID
        return "Bedroom"

    def list_devices(self, account_id: str) -> list[str]:
        assert account_id == ACCOUNT_ID
        return [DATASTORE_DEVICE_ID]

    def get_device_info(self, account_id: str, device_id: str):
        assert account_id == ACCOUNT_ID
        if device_id != DATASTORE_DEVICE_ID:
            raise AssertionError(f"unexpected datastore lookup for {device_id}")
        return SimpleNamespace(
            name="Living Room",
            product_code="SoundTouch30",
            device_id=DATASTORE_DEVICE_ID,
        )

    def get_presets(self, account_id: str) -> list[Preset]:
        assert account_id == ACCOUNT_ID
        return [
            Preset(
                id="1",
                name="Jazz FM",
                source="LOCAL_INTERNET_RADIO",
                type="STORED_MUSIC",
                location="jazz-fm",
                container_art="",
            )
        ]


class FakeSpeakers:
    def all_devices(self):
        return {
            DATASTORE_DEVICE_ID: SimpleNamespace(
                account=ACCOUNT_ID,
                online=True,
                in_soundcork=True,
                marge_server="Soundcork",
            ),
            DISCOVERED_ONLY_DEVICE_ID: SimpleNamespace(
                account=ACCOUNT_ID,
                online=True,
                in_soundcork=False,
                marge_server="Bose",
            ),
        }

    def get_volume(self, device_id: str):
        return None

    def get_now_playing(self, device_id: str):
        return None

    def get_all_zones(self, device_ids):
        return {}

    def get_all_power_states(self, device_ids):
        return {}

    def probe_reachability(self, device_ids):
        return set(device_ids)


class FakeTemplates:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.env = SimpleNamespace(globals={})

    def TemplateResponse(self, *args, **kwargs) -> Response:
        context = kwargs["context"]
        names = ",".join(device["name"] for device in context["devices"])
        return Response(names or "empty", media_type="text/html")


def test_dashboard_ignores_discovered_only_devices_not_in_datastore(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("soundcork.miniapp.Jinja2Templates", FakeTemplates)
    app = FastAPI()
    app.include_router(
        get_miniapp_router(cast(Any, FakeDatastore()), cast(Any, FakeSpeakers()))
    )

    client = TestClient(app)
    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; " "soundcork_account_label=Bedroom"
            )
        },
    )

    assert response.status_code == 200
    assert "Living Room" in response.text
    assert DISCOVERED_ONLY_DEVICE_ID not in response.text
