from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
