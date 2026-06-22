import logging
import socket
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)

from bosesoundtouchapi.models import Zone, ZoneMember  # type: ignore
from bosesoundtouchapi.soundtouchclient import (  # type: ignore
    ContentItem as BCContentItem,
    SoundTouchClient,
    SoundTouchDevice,
)
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from pydantic import BaseModel, ConfigDict
from urllib3 import PoolManager, Timeout  # type: ignore

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.model import ContentItem

logger = logging.getLogger(__name__)
DISCOVERY_TIMEOUT_SECONDS = 5

# Speaker-call timeouts. The default in bosesoundtouchapi is connect=30s with
# urllib3's retry policy doing 3 attempts ~3s apart, so a single call to a
# dead host costs ~12s. We override with a short connect timeout and disable
# retries; a single call against a dead host now fails in ~1.5s.
SPEAKER_HTTP_CONNECT_TIMEOUT_S = 1.5
SPEAKER_HTTP_READ_TIMEOUT_S = 5.0
SPEAKER_PORT_PROBE_TIMEOUT_S = 1.0
# How long to keep a device in the "known unreachable" cache after a failed
# call or port probe. Subsequent dashboard reads short-circuit during this
# window. Self-heals: a single successful call clears the entry.
UNREACHABLE_DEVICE_TTL_S = 30.0


class _FastFailPoolManager(PoolManager):
    """PoolManager that disables urllib3's retry-with-backoff for every
    request. Without this, a single call to a dead host costs 3 attempts
    spaced ~3 s apart (kernel ARP retries + urllib3 retries) = ~12 s. With
    retries=False, the failure surfaces on the first connect-timeout.
    """

    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("retries", False)
        return super().request(method, url, **kwargs)


def _is_connection_error(exc: BaseException) -> bool:
    """True if `exc` looks like a transient network failure — i.e. a reason
    to mark a device unreachable rather than surface to the caller as a
    real error. Walks the exception chain because bosesoundtouchapi wraps
    urllib3 errors in its own SoundTouchError."""
    msg = str(exc).lower()
    signals = (
        "no route to host",
        "host is down",
        "connection refused",
        "connection reset",
        "connection aborted",
        "name or service not known",
        "temporary failure in name resolution",
        "timed out",
        "max retries exceeded",
        "newconnectionerror",
        "connecttimeouterror",
        "readtimeouterror",
    )
    return any(signal in msg for signal in signals)


def _port_reachable(
    ip: str,
    port: int = 8090,
    timeout: float = SPEAKER_PORT_PROBE_TIMEOUT_S,
) -> bool:
    """Fast TCP connect to detect whether the speaker's HTTP API is
    reachable. Used to short-circuit dashboard reads against dead hosts
    before they pay the full connect-timeout cost."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# Sources that represent a device-local input (Bluetooth, line-in, AirPlay
# session etc.) rather than a streaming/preset source. When the device is
# on one of these, we display the source itself as the "now playing" label
# and never highlight a preset tile — the device often retains stale
# StationName/Track values from the previously played preset.
LOCAL_SOURCE_LABELS = {
    "BLUETOOTH": "Bluetooth",
    "AUX": "Aux In",
    "AIRPLAY": "AirPlay",
    "ALEXA": "Alexa",
    "NOTIFICATION": "Notification",
    "QPLAY": "QPlay",
    "UPNP": "UPnP",
    "STORED_MUSIC_MEDIA_RENDERER": "Stored Music",
    "OFF": "Off",
    "STANDBY": "Standby",
    "SLAVE_SOURCE": "Grouped",
}

# Sources where Next/Previous (AVRCP / UPnP transport-skip) makes sense.
# Radio streams (TuneIn etc.) don't have tracks; AUX/Alexa/notifications
# either have no transport or don't expose it.
TRACK_BASED_SOURCES = {
    "BLUETOOTH",
    "AIRPLAY",
    "AMAZON",
    "DEEZER",
    "SPOTIFY",
    "TIDAL",
    "QPLAY",
    "UPNP",
    "STORED_MUSIC",
    "STORED_MUSIC_MEDIA_RENDERER",
    "SOUNDCLOUD",
    "IHEART",
    "GOOGLE_PLAY_MUSIC",
    "PHONE_MUSIC",
}

# Sources where a "Resume play" command (MediaPlay) should be routed to
# the device's current source instead of re-triggering a preset.
LOCAL_TRANSPORT_SOURCES = {
    "BLUETOOTH",
    "AIRPLAY",
    "UPNP",
    "STORED_MUSIC",
    "STORED_MUSIC_MEDIA_RENDERER",
    "QPLAY",
}
# Skip a redundant UPnP rescan if one ran this recently. Force a fresh scan
# with refresh_discovery(force=True) when the user explicitly asks for it.
DISCOVERY_CACHE_TTL_SECONDS = 30

# Short post-action poll so snappy devices show their new state on the
# first dashboard render after the redirect. Slow devices (TuneIn streams
# can take 10-20s to buffer) are handled separately via a pending-action
# cookie + client-side dashboard polling, so we keep this very short — the
# important UX is that the user gets a redirect quickly with visible
# button feedback, not that we wait for the device to fully confirm.
STATE_POLL_MAX_ATTEMPTS = 3
STATE_POLL_INTERVAL_SECONDS = 0.15


class CombinedDevice(BaseModel):
    """Device: either detected, configured, or both

    A Device that's at least one of:
    - A physical SoundTouch speaker detected on the network
    - A configured DeviceInfo block stored in the datastore.

    Property:
    - id: Bose-issued unique speaker ID from DeviceInfo
    - ip: The speaker's IP address
    - name: Human-readable speaker name
    - online: Discoverable on the network as of last-update to this object. Not updated on disconnect.
    - account: Account ID
    - in_soundcork: In the soundcork datastore
    - marge_server: API this speaker uses for Marge: (ie. Bose, or this Soundcork instance)
    - reachable:  Has been configured (ie. with a USB key) to have shell-access available.
    - st_device: SoundTouchDevice instance as discovered by BoseSoundTouchApi
    """

    id: str
    ip: str
    name: str
    online: bool
    account: str
    in_soundcork: bool
    marge_server: str
    reachable: bool
    st_device: SoundTouchDevice | None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Speakers:
    """
    This class contains methods used to interact with speakers, primarily through the
    bosesoundtouchapi package (https://github.com/thlucas1/bosesoundtouchapi)
    """

    def __init__(self, datastore: DataStore, settings: Settings) -> None:
        self._st_discovery = SoundTouchDiscovery(areDevicesVerified=True)
        self._datastore = datastore
        self._settings = settings
        self._last_discovery_ts: float = 0.0
        # Per-Speakers shared http manager: short connect timeout + retries
        # disabled so a call against a dead host fails in ~1.5s instead of
        # ~12s. Shared across all speakers — urllib3 pools by host internally.
        self._http: PoolManager = _FastFailPoolManager(
            headers={"User-Agent": "soundcork/1.0"},
            timeout=Timeout(
                connect=SPEAKER_HTTP_CONNECT_TIMEOUT_S,
                read=SPEAKER_HTTP_READ_TIMEOUT_S,
            ),
            num_pools=10,
            maxsize=30,
            block=False,
        )
        # device_id -> monotonic expires_at. Devices known to be unreachable
        # are skipped by read paths until the TTL expires. User actions
        # bypass the cache and update it based on the result.
        self._unreachable: dict[str, float] = {}
        # Long-lived executor for parallel speaker reads (zone/power batch
        # calls). `as_completed(timeout=N)` lets the caller bail without
        # waiting on shutdown; stragglers keep running in the background.
        self._batch_pool = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="speakers-batch"
        )
        # Separate, smaller pool for fast port probes. Keeping these off
        # `_batch_pool` ensures that even when the batch pool is saturated
        # by slow speaker reads, a new probe still gets a worker
        # immediately — otherwise a probe could sit queued behind a stuck
        # read and then get falsely marked unreachable for timing out.
        self._probe_pool = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="speakers-probe"
        )
        self.refresh_discovery(force=True)

    # ------------------------------------------------------------------
    # Unreachable-cache + client factory (used by every speaker call)
    # ------------------------------------------------------------------

    def _mark_unreachable(self, device_id: str, reason: str = "") -> None:
        was_known = device_id in self._unreachable
        self._unreachable[device_id] = time.monotonic() + UNREACHABLE_DEVICE_TTL_S
        if not was_known:
            logger.info(
                f"Marked {device_id} unreachable for "
                f"{int(UNREACHABLE_DEVICE_TTL_S)}s"
                f"{f' ({reason})' if reason else ''}"
            )

    def _clear_unreachable(self, device_id: str) -> None:
        if self._unreachable.pop(device_id, None) is not None:
            logger.info(f"Cleared unreachable cache for {device_id}")

    def _is_unreachable(self, device_id: str) -> bool:
        expires_at = self._unreachable.get(device_id)
        if expires_at is None:
            return False
        if time.monotonic() >= expires_at:
            self._unreachable.pop(device_id, None)
            return False
        return True

    def _make_speaker_client(
        self,
        cd: "CombinedDevice | None",
        *,
        bypass_unreachable_cache: bool = False,
    ) -> SoundTouchClient | None:
        """Build a fast-fail SoundTouchClient bound to our shared
        connection pool.

        Returns None if `cd` is not a discovered speaker, or if the
        device is currently in the unreachable cache and the caller
        didn't ask to bypass it. Background/read paths use the cache;
        user-initiated actions (rename, play, switch source, …) bypass
        it so the user's click always at least gets attempted.
        """
        if not cd or not cd.st_device:
            return None
        if not bypass_unreachable_cache and self._is_unreachable(cd.id):
            return None
        return SoundTouchClient(cd.st_device, manager=self._http)

    def _record_speaker_call(self, device_id: str, exc: BaseException | None) -> None:
        if exc is None:
            self._clear_unreachable(device_id)
        elif _is_connection_error(exc):
            self._mark_unreachable(device_id, reason=type(exc).__name__)

    def _filter_to_reachable_ids(self, device_ids: list[str]) -> list[str]:
        """Return the subset of `device_ids` whose speaker HTTP API is
        currently reachable.

        Devices already known unreachable are skipped without probing.
        The rest get a fast parallel port-8090 probe on the dedicated
        `_probe_pool` (kept off `_batch_pool` so a saturated read pool
        can't starve probes). The cache is only updated when a probe
        actually RAN and returned a result — devices whose probe didn't
        get scheduled in time are passed through without poisoning the
        cache, so a contended probe queue can't falsely mark healthy
        devices as unreachable.
        """
        fresh = [did for did in device_ids if not self._is_unreachable(did)]
        if not fresh:
            return []
        cds = self.all_devices()
        candidates: list[tuple[str, str]] = []
        for did in fresh:
            cd = cds.get(did)
            if cd and cd.ip:
                candidates.append((did, cd.ip))
        if not candidates:
            return []

        futures = {
            self._probe_pool.submit(_port_reachable, ip): did for did, ip in candidates
        }
        timeout_s = SPEAKER_PORT_PROBE_TIMEOUT_S + 0.5
        survivors: list[str] = []
        for fut, did in futures.items():
            try:
                ok = fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                # Probe queued but didn't execute in time. Don't mark
                # unreachable — that would punish a healthy device for a
                # local thread-pool contention issue. Just skip it for
                # this batch; the next render gets a fresh chance.
                logger.debug(
                    f"probe for {did} did not complete within "
                    f"{timeout_s}s — treating as unknown, not unreachable"
                )
                continue
            except Exception:
                ok = False
            if ok:
                survivors.append(did)
            else:
                self._mark_unreachable(did, reason="port 8090 probe failed")
        return survivors

    def probe_reachability(self, device_ids: list[str]) -> set[str]:
        """Public entry point for callers that want to populate the
        unreachable-device cache before a batch of speaker reads.

        Used by the dashboard handler to do a single up-front probe pass
        across all online devices before calling get_volume /
        get_now_playing on the *selected* device, so the selected-device
        case also benefits from the fast-fail filter.
        """
        return set(self._filter_to_reachable_ids(device_ids))

    def refresh_discovery(self, force: bool = False) -> bool:
        """Run UPnP discovery, or skip it if a recent run is still cached.

        Returns True if a fresh scan was actually performed.
        """
        now = time.monotonic()
        if not force and (now - self._last_discovery_ts) < DISCOVERY_CACHE_TTL_SECONDS:
            return False
        self._st_discovery.DiscoverDevices(timeout=DISCOVERY_TIMEOUT_SECONDS)
        self._last_discovery_ts = now
        if force:
            # User explicitly asked to rescan — also forget what we thought
            # was unreachable. Anything actually dead will be re-detected.
            self._unreachable.clear()
        return True

    def discovery_age_seconds(self) -> float:
        """Seconds since the last successful discovery, for UI display."""
        if self._last_discovery_ts == 0.0:
            return float("inf")
        return time.monotonic() - self._last_discovery_ts

    def soundtouch_devices(self) -> dict:
        return self._st_discovery.VerifiedDevices

    def clear_device(self, device_id: str):
        cd = self.all_devices().get(device_id)
        if cd:
            st = cd.st_device
            if st:
                self._st_discovery.VerifiedDevices.pop(f"{st.Host}:8090")
                self._st_discovery.DiscoveredDeviceNames.pop(f"{st.Host}:8090")
        # If we evicted a device we'd previously marked unreachable, drop
        # the cache entry — otherwise it would linger past a real fix.
        self._clear_unreachable(device_id)

    def device_by_id(self, ip_port: str) -> SoundTouchDevice:
        logger.debug(f"Getting device by id: {ip_port}")
        return self._st_discovery.VerifiedDevices.get(ip_port)

    def all_devices(self) -> dict[str, CombinedDevice]:
        """
        Returns a combination of all devices seen on the network and
        all devices configured in soundcork as a dict with the device
        id as the key
        """
        combined_devices = {}
        account_ids = self._datastore.list_accounts()
        for account_id in account_ids:
            if account_id:
                for device_id in self._datastore.list_devices(account_id):
                    if device_id:
                        device_info = self._datastore.get_device_info(
                            account_id, device_id
                        )
                        cd = CombinedDevice(
                            # If the IP changes on a device reboot, it would have made a `/power_on`
                            # call to Soundcork, which will have already updated the datastore.
                            id=device_id,
                            ip=device_info.ip_address,
                            name=device_info.name,
                            online=False,
                            account=account_id,
                            in_soundcork=True,
                            marge_server="Unknown",
                            reachable=False,
                            st_device=None,
                        )
                        combined_devices[device_id] = cd
                        logger.debug(
                            f"cd for {device_id} = {combined_devices[device_id]}"
                        )

        verified = self.soundtouch_devices()
        for key in verified.keys():
            st_device = verified[key]
            id = st_device.DeviceId
            sc_device = combined_devices.get(id, None)

            if sc_device:
                sc_device.online = True
                sc_device.st_device = st_device
            else:
                # StreamingAccountUUID can be missing from /info on devices
                # that have never registered with Bose or that lost their
                # config — coerce to "" so the Pydantic model is happy.
                new_cd = CombinedDevice(
                    id=id,
                    ip=st_device.Host,
                    name=st_device.DeviceName,
                    online=True,
                    account=st_device.StreamingAccountUUID or "",
                    in_soundcork=False,
                    marge_server=st_device.StreamingUrl,
                    reachable=False,
                    st_device=st_device,
                )
                combined_devices[id] = new_cd
                sc_device = new_cd
            if st_device.StreamingUrl == "https://streaming.bose.com":
                sc_device.marge_server = "Bose"
            elif st_device.StreamingUrl == f"{self._settings.base_url}/marge":
                sc_device.marge_server = "Soundcork"
            else:
                sc_device.marge_server = f"Unknown ({st_device.StreamingUrl})"

        return combined_devices

    def _content_item_to_soundtouchclient(self, ci: ContentItem) -> BCContentItem:
        """Maps our ContentItem to a SoundTouchClient ContentItem."""
        return BCContentItem(
            name=ci.name,
            source=ci.source,
            typeValue=ci.type,
            location=ci.location,
            sourceAccount=ci.source_account,
            isPresetable=ci.is_presetable,
        )

    def play_content_item(self, device_id: str, content_item_id: str) -> bool:
        """Play a content_item on a specific device.

        Args:
            device_id: The device ID to play on
            content_item: The content item ID to play

        Returns:
            True if successful, False otherwise
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            logger.error(f"Device {device_id} not found or not online")
            return False

        content_item = self._datastore.get_content_item(
            account=cd.account if cd else "",
            device_id=cd.id if cd else device_id,
            ci_id=content_item_id,
        )
        if not content_item:
            logger.error(f"{content_item_id} is not a defined ContentItem")
            return False

        logger.info(
            f"Attempting playback of content item {content_item_id} on device {device_id}"
        )
        bose_content_item = self._content_item_to_soundtouchclient(content_item)

        try:
            client.PlayContentItem(bose_content_item)
            self._record_speaker_call(device_id, None)
        except Exception as e:
            logger.error(f"PlayContentItem failed: {e}")
            self._record_speaker_call(device_id, e)
            return False

        self._wait_for_play_state(device_id, expected_playing=True)
        return True

    def _wait_for_play_state(self, device_id: str, expected_playing: bool) -> None:
        """Poll device until its play state matches expected (or timeout).

        Bose devices take ~0.5-2s to transition after a play/stop command.
        Without this, the immediate dashboard redirect renders stale state.
        """
        for _ in range(STATE_POLL_MAX_ATTEMPTS):
            time.sleep(STATE_POLL_INTERVAL_SECONDS)
            np = self.get_now_playing(device_id)
            if np is not None and np["is_playing"] == expected_playing:
                return

    def stop_playback(self, device_id: str) -> bool:
        """Stop playback on a specific device.

        Args:
            device_id: The device ID to stop

        Returns:
            True if successful, False otherwise
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            logger.error(f"Device {device_id} not found or not online")
            return False

        try:
            client.MediaStop()
            self._record_speaker_call(device_id, None)
            logger.info(f"Stopped playback on device {device_id}")
        except Exception as e:
            logger.error(f"Error stopping playback on device {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

        self._wait_for_play_state(device_id, expected_playing=False)
        return True

    def select_source(
        self, device_id: str, source: str, source_account: str = ""
    ) -> bool:
        """Switch a device to a local input source (BLUETOOTH, AUX, etc.)."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            logger.error(f"Device {device_id} not found or not online")
            return False
        try:
            item = BCContentItem(
                source=source,
                sourceAccount=source_account or None,
            )
            client.SelectContentItem(item)
            self._record_speaker_call(device_id, None)
            logger.info(
                f"Switched device {device_id} to source {source}"
                f"{f' (account={source_account})' if source_account else ''}"
            )
            return True
        except Exception as e:
            logger.error(f"Error switching device {device_id} to source {source}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def get_now_playing(self, device_id: str) -> dict | None:
        """Get the device's current playback state.

        Returns:
            dict with `content_name` (str|None), `source` (str), `play_state` (str),
            `is_playing` (bool), `is_local_source` (bool — True for BT/AUX/etc.),
            or None on failure.
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd)
        if not client:
            return None
        try:
            np = client.GetNowPlayingStatus()
            self._record_speaker_call(device_id, None)
            play_state = (getattr(np, "PlayStatus", "") or "").upper()
            source = (getattr(np, "Source", "") or "").upper()
            is_local = source in LOCAL_SOURCE_LABELS

            track = (getattr(np, "Track", "") or "").strip() or None
            artist = (getattr(np, "Artist", "") or "").strip() or None
            is_playing = play_state in {
                "PLAY_STATE",
                "BUFFERING_STATE",
                "PLAY",
                "BUFFERING",
            }
            source_label = LOCAL_SOURCE_LABELS.get(source) if is_local else None
            artist_out = None

            if is_local:
                if source in TRACK_BASED_SOURCES and is_playing and (track or artist):
                    # BT/AirPlay/UPnP/etc. broadcast track metadata via AVRCP
                    # — use it as the headline. Fall back to source label.
                    content_name = track or artist or LOCAL_SOURCE_LABELS[source]
                    artist_out = artist if track and artist else None
                else:
                    # AUX/idle/etc. — show the source name itself. Skip the
                    # stale StationName the device may have kept.
                    content_name = LOCAL_SOURCE_LABELS[source]
            else:
                content_name = (
                    (getattr(np, "StationName", "") or "").strip()
                    or track
                    or artist
                    or None
                )

            return {
                "content_name": content_name,
                "artist": artist_out,
                "source": source,
                "source_label": source_label,
                "is_local_source": is_local,
                "supports_skip": source in TRACK_BASED_SOURCES,
                "supports_local_resume": source in LOCAL_TRANSPORT_SOURCES,
                "play_state": play_state,
                "is_playing": is_playing,
            }
        except Exception as e:
            logger.error(f"Error getting now-playing on device {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return None

    def get_zone(self, device_id: str) -> dict | None:
        """Get the device's current multi-room zone state.

        Returns a dict with `master_device_id`, `is_master` and a `members`
        list (each `{device_id, ip, role}`), or `None` if the device is
        solo / unreachable. A solo device returns None even though
        GetZoneStatus may succeed — we treat "empty zone" as "not in a zone".
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd)
        if not client:
            return None
        try:
            zone = client.GetZoneStatus()
            self._record_speaker_call(device_id, None)
        except Exception as e:
            logger.debug(f"Error getting zone for {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return None

        master_id = getattr(zone, "MasterDeviceId", None) or ""
        if not master_id:
            return None  # not in a zone
        members = []
        for m in getattr(zone, "Members", None) or []:
            members.append(
                {
                    "device_id": getattr(m, "DeviceId", "") or "",
                    "ip": getattr(m, "IpAddress", "") or "",
                    "role": getattr(m, "DeviceRole", "") or "",
                }
            )
        # Bose's `senderIsMaster` flag is unreliable (we've seen it set true
        # on a slave's response). The trustworthy way to tell if THIS device
        # is the zone master is to compare its id against master_device_id.
        return {
            "master_device_id": master_id,
            "is_master": master_id == device_id,
            "members": members,
        }

    def get_all_zones(self, device_ids: list[str]) -> dict[str, dict]:
        """Query zone state for many devices in parallel.

        Returns `{device_id: zone_dict}` only for devices that ARE in a zone;
        solo devices are omitted. Unreachable devices are filtered out by a
        fast port-8090 probe before we make any speaker calls. Uses the
        long-lived `_batch_pool` so stragglers never block the caller.
        """
        if not device_ids:
            return {}
        reachable_ids = self._filter_to_reachable_ids(device_ids)
        if not reachable_ids:
            return {}
        futures = {
            self._batch_pool.submit(self.get_zone, did): did for did in reachable_ids
        }
        result: dict[str, dict] = {}
        try:
            for fut in as_completed(futures, timeout=3.0):
                did = futures[fut]
                try:
                    z = fut.result()
                except Exception:
                    z = None
                if z:
                    result[did] = z
        except FuturesTimeoutError:
            # Stragglers stay running in the background — we just don't
            # wait on them. They'll mark themselves unreachable via the
            # _record_speaker_call() path on their own exception.
            pass
        return result

    def set_power_state(self, device_id: str, on: bool) -> bool:
        """Power a speaker on (wake from standby) or put it into standby.

        PowerOn() reads the current NowPlayingStatus; if source == "STANDBY"
        it sends the POWER key — so calling it on an already-on speaker is
        a no-op. PowerStandby() PUTs `/standby` and works regardless.
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            if on:
                client.PowerOn()
            else:
                client.PowerStandby()
            self._record_speaker_call(device_id, None)
            logger.info(f"set_power_state {device_id} -> {'on' if on else 'standby'}")
            return True
        except Exception as e:
            logger.error(
                f"set_power_state {device_id} -> {'on' if on else 'standby'} "
                f"failed: {e}"
            )
            self._record_speaker_call(device_id, e)
            return False

    def get_all_power_states(self, device_ids: list[str]) -> dict[str, bool]:
        """Returns `{device_id: is_on}` for the given speakers, in parallel.

        A speaker is "on" when its NowPlayingStatus source is anything other
        than STANDBY / empty. Unreachable devices are filtered out by a
        fast port-8090 probe before we make any speaker calls. Uses the
        long-lived `_batch_pool` so stragglers never block the caller.
        """
        if not device_ids:
            return {}
        reachable_ids = self._filter_to_reachable_ids(device_ids)
        if not reachable_ids:
            return {}

        def _check(did: str) -> tuple[str, bool]:
            np = self.get_now_playing(did)
            source = ((np or {}).get("source") or "").upper()
            return did, bool(source) and source != "STANDBY"

        futures = {self._batch_pool.submit(_check, did): did for did in reachable_ids}
        result: dict[str, bool] = {}
        try:
            for fut in as_completed(futures, timeout=3.0):
                try:
                    did_out, is_on = fut.result()
                    result[did_out] = is_on
                except Exception:
                    continue
        except FuturesTimeoutError:
            pass
        return result

    def group_toggle(self, primary_id: str, other_id: str) -> bool:
        """Make `primary` and `other` share a zone, or undo if already shared.

        - If `other` already shares `primary`'s zone, `other` is removed from it.
        - Otherwise `other` is added to `primary`'s zone (creating one with
          `primary` as master if needed). If `other` was in a different zone,
          it's removed from that one first.
        """
        if primary_id == other_id:
            return False
        primary = self.all_devices().get(primary_id)
        other = self.all_devices().get(other_id)
        if not primary or not primary.st_device:
            return False
        if not other or not other.st_device:
            return False

        primary_zone = self.get_zone(primary_id)
        other_zone = self.get_zone(other_id)

        same_zone = (
            primary_zone
            and other_zone
            and primary_zone["master_device_id"] == other_zone["master_device_id"]
        )
        if same_zone:
            return self._remove_from_zone(other_id, other_zone)

        # If `other` is in a different zone, evict it first.
        if other_zone:
            self._remove_from_zone(other_id, other_zone)

        try:
            if primary_zone:
                # Add `other` to the existing zone (call the master).
                master_id = primary_zone["master_device_id"]
                master = self.all_devices().get(master_id)
                client = self._make_speaker_client(
                    master, bypass_unreachable_cache=True
                )
                if not client:
                    return False
                client.AddZoneMembers(
                    [ZoneMember(ipAddress=other.ip, deviceId=other_id)]
                )
                self._record_speaker_call(master_id, None)
                logger.info(f"Added device {other_id} to zone mastered by {master_id}")
                self._sync_zone_volume(master_id, [other_id])
            else:
                # Create a new zone with `primary` as master, `other` as slave.
                client = self._make_speaker_client(
                    primary, bypass_unreachable_cache=True
                )
                if not client:
                    return False
                client.CreateZoneFromDevices(primary.st_device, [other.st_device])
                self._record_speaker_call(primary_id, None)
                logger.info(f"Created new zone: master={primary_id}, slave={other_id}")
                self._sync_zone_volume(primary_id, [other_id])
            return True
        except Exception as e:
            logger.error(f"group_toggle failed for {primary_id}+{other_id}: {e}")
            # The call was issued against whichever speaker was acting as the
            # zone master for this operation. Record the failure against it
            # so future reads can short-circuit if it's actually unreachable.
            target_id = primary_zone["master_device_id"] if primary_zone else primary_id
            self._record_speaker_call(target_id, e)
            return False

    def _sync_zone_volume(self, master_id: str, slave_ids: list[str]) -> None:
        """Best-effort: align every slave's volume with the master so the
        group plays in sync. Failures are swallowed — the group still works,
        it just plays at the slaves' previous volumes.
        """
        try:
            master_vol = self.get_volume(master_id)
            if not master_vol or master_vol.get("actual") is None:
                return
            target = int(master_vol["actual"])
            for slave_id in slave_ids:
                slave_vol = self.get_volume(slave_id)
                if slave_vol and int(slave_vol.get("actual", -1)) == target:
                    continue
                self.set_volume(slave_id, target)
                logger.info(
                    f"Synced volume on {slave_id} to {target} (master {master_id})"
                )
        except Exception as e:
            logger.debug(f"volume sync from {master_id} failed: {e}")

    def _remove_from_zone(self, device_id: str, zone: dict | None = None) -> bool:
        """Remove `device_id` from its current multi-room zone."""
        if zone is None:
            zone = self.get_zone(device_id)
        if not zone:
            logger.info(f"_remove_from_zone: {device_id} is already solo")
            return True

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error(f"_remove_from_zone: {device_id} has no st_device")
            return False

        logger.info(
            f"_remove_from_zone: device={device_id} is_master={zone['is_master']} "
            f"master={zone['master_device_id']} members={zone['members']}"
        )

        try:
            if zone["is_master"]:
                # Removing the master tears down the zone — pull every slave.
                client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
                if not client:
                    return False
                members = [
                    ZoneMember(ipAddress=m["ip"], deviceId=m["device_id"])
                    for m in zone["members"]
                    if m["device_id"] and m["device_id"] != device_id
                ]
                logger.info(
                    f"_remove_from_zone: master {device_id} pulling slaves "
                    f"{[m.DeviceId for m in members]}"
                )
                if members:
                    client.RemoveZoneMembers(members)
                self._record_speaker_call(device_id, None)
                logger.info(f"Disbanded zone mastered by {device_id}")
                return True

            # Slave — ask the master to remove just this slave.
            master_id = zone["master_device_id"]
            master = self.all_devices().get(master_id)
            client = self._make_speaker_client(master, bypass_unreachable_cache=True)
            if not client:
                logger.error(
                    f"_remove_from_zone: master {master_id} of slave "
                    f"{device_id} has no st_device — cannot detach"
                )
                return False
            slave_member = ZoneMember(ipAddress=cd.ip, deviceId=device_id)
            logger.info(
                f"_remove_from_zone: asking master {master_id} "
                f"({master.ip if master else '?'}) "
                f"to drop slave {device_id} ({cd.ip})"
            )
            client.RemoveZoneMembers([slave_member])
            self._record_speaker_call(master_id, None)
            logger.info(f"Removed slave {device_id} from zone mastered by {master_id}")
            return True
        except Exception as e:
            logger.exception(
                f"Could not remove {device_id} from zone "
                f"{zone.get('master_device_id')}"
            )
            # The call was issued against the master (whether self or another
            # speaker). Record the failure against it so we don't keep
            # hitting a dead host on subsequent attempts.
            target_id = device_id if zone["is_master"] else zone["master_device_id"]
            self._record_speaker_call(target_id, e)
            return False

    def ungroup_device(self, device_id: str) -> bool:
        """Public wrapper — used by the dashboard's per-card "Leave group" button."""
        return self._remove_from_zone(device_id)

    def rename_device(self, device_id: str, new_name: str) -> bool:
        """Push a new friendly name to the speaker via the SoundTouch API.

        Updates the speaker's local `/info`, `/getName`, and UPnP
        `friendlyName` in one call. Caller is still responsible for
        updating soundcork's stored DeviceInfo.xml so that subsequent
        Marge responses match.
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            client.SetName(new_name)
            self._record_speaker_call(device_id, None)
            logger.info(f"Renamed device {device_id} to {new_name!r}")
            return True
        except Exception as e:
            logger.error(f"SetName failed on {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def media_play(self, device_id: str) -> bool:
        """Resume playback on the device's current source (AVRCP/UPnP play)."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            client.MediaPlay()
            self._record_speaker_call(device_id, None)
            logger.info(f"Sent media-play to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-play to {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def media_next(self, device_id: str) -> bool:
        """Skip to the next track on the device's current source."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            client.MediaNextTrack()
            self._record_speaker_call(device_id, None)
            logger.info(f"Sent media-next to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-next to {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def media_previous(self, device_id: str) -> bool:
        """Skip to the previous track on the device's current source."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            client.MediaPreviousTrack()
            self._record_speaker_call(device_id, None)
            logger.info(f"Sent media-previous to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-previous to {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def get_volume(self, device_id: str) -> dict | None:
        """Get the current volume state of a device.

        Returns:
            dict with keys 'actual' (int 0-100) and 'muted' (bool), or None on failure
        """
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd)
        if not client:
            return None
        try:
            vol = client.GetVolume()
            self._record_speaker_call(device_id, None)
            return {
                "actual": getattr(vol, "Actual", 0),
                "muted": getattr(vol, "IsMuted", False),
            }
        except Exception as e:
            logger.error(f"Error getting volume on device {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return None

    def set_volume(self, device_id: str, level: int) -> bool:
        """Set the volume level (0-100) on a device."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        level = max(0, min(100, int(level)))
        try:
            client.SetVolumeLevel(level)
            self._record_speaker_call(device_id, None)
            logger.info(f"Set volume to {level} on device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting volume on device {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False

    def toggle_mute(self, device_id: str) -> bool:
        """Toggle mute on a device."""
        cd = self.all_devices().get(device_id)
        client = self._make_speaker_client(cd, bypass_unreachable_cache=True)
        if not client:
            return False
        try:
            client.Mute()
            self._record_speaker_call(device_id, None)
            logger.info(f"Toggled mute on device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error toggling mute on device {device_id}: {e}")
            self._record_speaker_call(device_id, e)
            return False
