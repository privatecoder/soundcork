from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from soundcork.admin import get_admin_router


class FakeDatastore:
    def __init__(self) -> None:
        self.saved_device: tuple[str, Any] | None = None

    def list_accounts(self) -> list[str]:
        return []

    def device_exists(self, account_id: str, device_id: str) -> bool:
        return account_id == "7679292" and device_id == "C8DF84AC5AB1"

    def get_device_info(self, account_id: str, device_id: str):
        return SimpleNamespace(
            device_id=device_id,
            name="Old name",
            ip_address="192.168.1.42",
            product_code="SoundTouch 20",
        )

    def save_device_info(self, device, account_id: str):
        self.saved_device = (account_id, device)
        return device


class FakeSpeakers:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.forced_refresh_calls = 0
        self.renamed_devices: list[tuple[str, str]] = []
        self.cleared_devices: list[str] = []
        self.invalidate_calls = 0
        self.devices: dict[str, Any] = {}

    def refresh_discovery(self, force: bool = False) -> bool:
        self.refresh_calls += 1
        if force:
            self.forced_refresh_calls += 1
        return True

    def all_devices(self) -> dict[str, Any]:
        return self.devices

    def rename_device(self, device_id: str, new_name: str) -> bool:
        self.renamed_devices.append((device_id, new_name))
        return True

    def clear_device(self, device_id: str):
        self.cleared_devices.append(device_id)

    def invalidate_devices_cache(self) -> None:
        self.invalidate_calls += 1


class FakeSettings:
    base_url = "http://192.168.1.50:8000"


class FakeTemplates:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.env = SimpleNamespace(globals={})

    def TemplateResponse(self, *args, **kwargs):
        from fastapi import Response

        return Response("ok", media_type="text/html")


def test_admin_renders_without_refreshing(monkeypatch):
    """The shell page should not trigger discovery; only the fragment does."""
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("fastapi.templating.Jinja2Templates", FakeTemplates)
    monkeypatch.setattr("soundcork.admin.addr_is_reachable", lambda _ip: False)
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
    monkeypatch.setattr("soundcork.admin.addr_is_reachable", lambda _ip: False)
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


def test_rename_device_updates_speaker_and_datastore_without_clearing_cache(
    monkeypatch,
):
    """Rename should not evict discovery cache and make the device appear offline."""
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setattr("fastapi.templating.Jinja2Templates", FakeTemplates)
    app = FastAPI()
    datastore = FakeDatastore()
    speakers = FakeSpeakers()
    speakers.devices = {
        "C8DF84AC5AB1": SimpleNamespace(
            online=True,
            st_device=object(),
            account="7679292",
        )
    }
    app.include_router(
        get_admin_router(
            cast(Any, datastore),
            cast(Any, speakers),
            cast(Any, FakeSettings()),
        )
    )

    client = TestClient(app)
    response = client.post(
        "/admin/renameDevice/C8DF84AC5AB1",
        data={"name": "New name"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert speakers.renamed_devices == [("C8DF84AC5AB1", "New name")]
    assert speakers.cleared_devices == []
    assert datastore.saved_device is not None
    account_id, device = datastore.saved_device
    assert account_id == "7679292"
    assert device.name == "New name"
    # The datastore name changed, so the all_devices() memo must be dropped
    # (and the broad except must not have swallowed a missing-method error).
    assert speakers.invalidate_calls == 1
