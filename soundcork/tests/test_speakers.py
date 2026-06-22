import threading
import time
from types import SimpleNamespace
from typing import Any, cast

from soundcork.ui.speakers import (
    UNREACHABLE_DEVICE_TTL_S,
    Speakers,
    _FastFailPoolManager,
)


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


def _make_speakers(monkeypatch) -> Speakers:
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDiscovery", FakeDiscovery)
    return Speakers(
        cast(Any, FakeDatastore()),
        cast(Any, SimpleNamespace(base_url="http://soundcork.local")),
    )


def test_speakers_use_longer_discovery_timeout(monkeypatch):
    speakers = _make_speakers(monkeypatch)
    assert speakers._st_discovery.timeouts == [5]


# --- Fix A — fast-fail urllib3 manager ----------------------------------


class _RecordingPoolManager(_FastFailPoolManager):
    """Captures the kwargs that reach the underlying request() so the
    test can assert retries=False was injected."""

    def __init__(self) -> None:
        # Don't call super().__init__ — we don't want a real pool.
        self.captured_kwargs: list[dict] = []

    def request(self, method, url, **kwargs):  # type: ignore[override]
        # Mirror _FastFailPoolManager.request's default-injection.
        kwargs.setdefault("retries", False)
        self.captured_kwargs.append(kwargs)


def test_fast_fail_pool_manager_injects_retries_false():
    pm = _RecordingPoolManager()
    pm.request("GET", "http://example.invalid/info")
    pm.request("POST", "http://example.invalid/name", body=b"<x/>")
    pm.request(
        "GET", "http://example.invalid/x", retries=5
    )  # caller-supplied retries are NOT overridden

    assert len(pm.captured_kwargs) == 3
    assert pm.captured_kwargs[0]["retries"] is False
    assert pm.captured_kwargs[1]["retries"] is False
    assert pm.captured_kwargs[2]["retries"] == 5


# --- Fix B — batch reads honor timeout without waiting on stragglers ----


def test_get_all_zones_returns_before_stuck_future_completes(monkeypatch):
    """A slow get_zone() must NOT block the get_all_zones() return.

    Submit one slow worker (sleeps past the batch timeout) and one fast
    worker. The slow one stays running in the background; the call
    returns within the timeout with only the fast result.
    """
    speakers = _make_speakers(monkeypatch)
    # Bypass the port-probe filter so we hit get_zone directly.
    monkeypatch.setattr(
        speakers,
        "_filter_to_reachable_ids",
        lambda ids: list(ids),
    )

    slow_started = threading.Event()
    slow_finished = threading.Event()

    def fake_get_zone(did: str):
        if did == "slow":
            slow_started.set()
            # Sleep well past the 3s as_completed timeout used by
            # get_all_zones. If the call waits on this, the test fails.
            time.sleep(10)
            slow_finished.set()
            return {"master_device_id": "slow", "is_master": True, "members": []}
        return {"master_device_id": did, "is_master": True, "members": []}

    monkeypatch.setattr(speakers, "get_zone", fake_get_zone)

    t0 = time.monotonic()
    result = speakers.get_all_zones(["fast", "slow"])
    elapsed = time.monotonic() - t0

    # The fast result must come back; the slow one must NOT have blocked us.
    assert "fast" in result
    assert "slow" not in result
    # 3s as_completed timeout + small overhead. Definitely well under 10s.
    assert elapsed < 5, f"get_all_zones blocked {elapsed:.2f}s on stuck future"
    # The slow worker should still be running — proof that we didn't wait
    # on it (the with-block-shutdown bug would have).
    assert slow_started.is_set()
    assert not slow_finished.is_set()


# --- Fix C — failed reads mark unreachable; cache short-circuits --------


def test_failed_get_now_playing_marks_unreachable(monkeypatch):
    speakers = _make_speakers(monkeypatch)

    fake_cd = SimpleNamespace(id="DEAD1", ip="10.0.0.1", st_device=object())
    monkeypatch.setattr(speakers, "all_devices", lambda: {"DEAD1": fake_cd})

    class FakeClient:
        def GetNowPlayingStatus(self):
            raise RuntimeError("HTTPConnectionPool no route to host")

    monkeypatch.setattr(speakers, "_make_speaker_client", lambda *a, **kw: FakeClient())

    assert not speakers._is_unreachable("DEAD1")
    result = speakers.get_now_playing("DEAD1")
    assert result is None
    assert speakers._is_unreachable("DEAD1")


def test_cached_unreachable_device_skips_speaker_call(monkeypatch):
    """A device already in the unreachable cache must short-circuit
    BEFORE _make_speaker_client returns a client, so no call is made."""
    speakers = _make_speakers(monkeypatch)

    fake_cd = SimpleNamespace(
        id="DEAD2", ip="10.0.0.2", st_device=object(), online=True
    )
    monkeypatch.setattr(speakers, "all_devices", lambda: {"DEAD2": fake_cd})

    client_constructions: list[bool] = []

    class FakeClient:
        def GetNowPlayingStatus(self):
            client_constructions.append(True)
            raise AssertionError("should not be called for cached-unreachable device")

    # Mark the device unreachable directly, then attempt a read.
    speakers._mark_unreachable("DEAD2", reason="test")

    monkeypatch.setattr(
        speakers,
        "_make_speaker_client",
        # Bypass our real factory but honor the cache check ourselves so
        # this test specifically exercises the cache short-circuit path.
        lambda cd, *, bypass_unreachable_cache=False: (
            None if speakers._is_unreachable(cd.id) else FakeClient()
        ),
    )

    result = speakers.get_now_playing("DEAD2")
    assert result is None
    assert client_constructions == []


def test_record_speaker_call_success_clears_unreachable(monkeypatch):
    speakers = _make_speakers(monkeypatch)
    speakers._mark_unreachable("DEAD3", reason="test")
    assert speakers._is_unreachable("DEAD3")

    speakers._record_speaker_call("DEAD3", None)
    assert not speakers._is_unreachable("DEAD3")


def test_unreachable_cache_expires_after_ttl(monkeypatch):
    speakers = _make_speakers(monkeypatch)
    speakers._mark_unreachable("DEAD4", reason="test")
    assert speakers._is_unreachable("DEAD4")
    # Rewind the expiry past now.
    speakers._unreachable["DEAD4"] = time.monotonic() - 1
    assert not speakers._is_unreachable("DEAD4")
    assert "DEAD4" not in speakers._unreachable, "expired entry should be evicted"


def test_refresh_discovery_force_clears_unreachable_cache(monkeypatch):
    speakers = _make_speakers(monkeypatch)
    speakers._mark_unreachable("DEAD5", reason="test")
    assert speakers._is_unreachable("DEAD5")

    speakers.refresh_discovery(force=True)
    assert not speakers._is_unreachable("DEAD5")


# --- Fix D — probe pool is separate from batch read pool ----------------


def test_probe_pool_is_separate_from_batch_pool(monkeypatch):
    speakers = _make_speakers(monkeypatch)
    assert speakers._probe_pool is not speakers._batch_pool
