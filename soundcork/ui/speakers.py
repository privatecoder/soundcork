import logging
import time
from concurrent.futures import ThreadPoolExecutor

from bosesoundtouchapi.models import Zone, ZoneMember  # type: ignore
from bosesoundtouchapi.soundtouchclient import (  # type: ignore
    ContentItem as BCContentItem,
    SoundTouchClient,
    SoundTouchDevice,
)
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from pydantic import BaseModel, ConfigDict

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.model import ContentItem

logger = logging.getLogger(__name__)
DISCOVERY_TIMEOUT_SECONDS = 5

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
        self.refresh_discovery(force=True)

    def refresh_discovery(self, force: bool = False) -> bool:
        """Run UPnP discovery, or skip it if a recent run is still cached.

        Returns True if a fresh scan was actually performed.
        """
        now = time.monotonic()
        if not force and (now - self._last_discovery_ts) < DISCOVERY_CACHE_TTL_SECONDS:
            return False
        self._st_discovery.DiscoverDevices(timeout=DISCOVERY_TIMEOUT_SECONDS)
        self._last_discovery_ts = now
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
                new_cd = CombinedDevice(
                    id=id,
                    ip=st_device.Host,
                    name=st_device.DeviceName,
                    online=True,
                    account=st_device.StreamingAccountUUID,
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
        if not cd or not cd.st_device:
            logger.error(f"Device {device_id} not found or not online")
            return False

        content_item = self._datastore.get_content_item(
            account=cd.account,
            device_id=cd.id,
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
            client = SoundTouchClient(cd.st_device)
            client.PlayContentItem(bose_content_item)
        except Exception as e:
            logger.error(f"PlayContentItem failed: {e}")
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
        if not cd or not cd.st_device:
            logger.error(f"Device {device_id} not found or not online")
            return False

        client = SoundTouchClient(cd.st_device)
        try:
            client.MediaStop()
            logger.info(f"Stopped playback on device {device_id}")
        except Exception as e:
            logger.error(f"Error stopping playback on device {device_id}: {e}")
            return False

        self._wait_for_play_state(device_id, expected_playing=False)
        return True

    def select_source(
        self, device_id: str, source: str, source_account: str = ""
    ) -> bool:
        """Switch a device to a local input source (BLUETOOTH, AUX, etc.)."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error(f"Device {device_id} not found or not online")
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            item = BCContentItem(
                source=source,
                sourceAccount=source_account or None,
            )
            client.SelectContentItem(item)
            logger.info(
                f"Switched device {device_id} to source {source}"
                f"{f' (account={source_account})' if source_account else ''}"
            )
            return True
        except Exception as e:
            logger.error(f"Error switching device {device_id} to source {source}: {e}")
            return False

    def get_now_playing(self, device_id: str) -> dict | None:
        """Get the device's current playback state.

        Returns:
            dict with `content_name` (str|None), `source` (str), `play_state` (str),
            `is_playing` (bool), `is_local_source` (bool — True for BT/AUX/etc.),
            or None on failure.
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return None
        try:
            client = SoundTouchClient(cd.st_device)
            np = client.GetNowPlayingStatus()
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
            return None

    def get_zone(self, device_id: str) -> dict | None:
        """Get the device's current multi-room zone state.

        Returns a dict with `master_device_id`, `is_master` and a `members`
        list (each `{device_id, ip, role}`), or `None` if the device is
        solo / unreachable. A solo device returns None even though
        GetZoneStatus may succeed — we treat "empty zone" as "not in a zone".
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return None
        try:
            client = SoundTouchClient(cd.st_device)
            zone = client.GetZoneStatus()
        except Exception as e:
            logger.debug(f"Error getting zone for {device_id}: {e}")
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
        return {
            "master_device_id": master_id,
            "is_master": bool(getattr(zone, "IsZoneMaster", False)),
            "members": members,
        }

    def get_all_zones(self, device_ids: list[str]) -> dict[str, dict]:
        """Query zone state for many devices in parallel.

        Returns `{device_id: zone_dict}` only for devices that ARE in a zone;
        solo devices are omitted. Slow devices are dropped at ~3s.
        """
        if not device_ids:
            return {}
        result: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(device_ids))) as pool:
            futures = {pool.submit(self.get_zone, did): did for did in device_ids}
            for fut, did in futures.items():
                try:
                    z = fut.result(timeout=3)
                except Exception:
                    z = None
                if z:
                    result[did] = z
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
                if not master or not master.st_device:
                    return False
                client = SoundTouchClient(master.st_device)
                client.AddZoneMembers(
                    [ZoneMember(ipAddress=other.ip, deviceId=other_id)]
                )
                logger.info(
                    f"Added device {other_id} to zone mastered by {master_id}"
                )
            else:
                # Create a new zone with `primary` as master, `other` as slave.
                client = SoundTouchClient(primary.st_device)
                client.CreateZoneFromDevices(primary.st_device, [other.st_device])
                logger.info(
                    f"Created new zone: master={primary_id}, slave={other_id}"
                )
            return True
        except Exception as e:
            logger.error(f"group_toggle failed for {primary_id}+{other_id}: {e}")
            return False

    def _remove_from_zone(self, device_id: str, zone: dict | None = None) -> bool:
        """Remove `device_id` from its current multi-room zone."""
        if zone is None:
            zone = self.get_zone(device_id)
        if not zone:
            return True  # already solo

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False

        try:
            if zone["is_master"]:
                # Removing the master tears down the zone — pull every slave.
                client = SoundTouchClient(cd.st_device)
                members = [
                    ZoneMember(ipAddress=m["ip"], deviceId=m["device_id"])
                    for m in zone["members"]
                    if m["device_id"] and m["device_id"] != device_id
                ]
                if members:
                    client.RemoveZoneMembers(members)
                logger.info(f"Disbanded zone mastered by {device_id}")
                return True

            # Slave — ask the master to remove it.
            master_id = zone["master_device_id"]
            master = self.all_devices().get(master_id)
            if not master or not master.st_device:
                return False
            client = SoundTouchClient(master.st_device)
            client.RemoveZoneMembers(
                [ZoneMember(ipAddress=cd.ip, deviceId=device_id)]
            )
            logger.info(f"Removed device {device_id} from zone {master_id}")
            return True
        except Exception as e:
            logger.error(f"Could not remove {device_id} from zone: {e}")
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
        if not cd or not cd.st_device:
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            client.SetName(new_name)
            logger.info(f"Renamed device {device_id} to {new_name!r}")
            return True
        except Exception as e:
            logger.error(f"SetName failed on {device_id}: {e}")
            return False

    def media_play(self, device_id: str) -> bool:
        """Resume playback on the device's current source (AVRCP/UPnP play)."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            client.MediaPlay()
            logger.info(f"Sent media-play to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-play to {device_id}: {e}")
            return False

    def media_next(self, device_id: str) -> bool:
        """Skip to the next track on the device's current source."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            client.MediaNextTrack()
            logger.info(f"Sent media-next to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-next to {device_id}: {e}")
            return False

    def media_previous(self, device_id: str) -> bool:
        """Skip to the previous track on the device's current source."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            client.MediaPreviousTrack()
            logger.info(f"Sent media-previous to device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending media-previous to {device_id}: {e}")
            return False

    def get_volume(self, device_id: str) -> dict | None:
        """Get the current volume state of a device.

        Returns:
            dict with keys 'actual' (int 0-100) and 'muted' (bool), or None on failure
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return None
        try:
            client = SoundTouchClient(cd.st_device)
            vol = client.GetVolume()
            return {
                "actual": getattr(vol, "Actual", 0),
                "muted": getattr(vol, "IsMuted", False),
            }
        except Exception as e:
            logger.error(f"Error getting volume on device {device_id}: {e}")
            return None

    def set_volume(self, device_id: str, level: int) -> bool:
        """Set the volume level (0-100) on a device."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False
        level = max(0, min(100, int(level)))
        try:
            client = SoundTouchClient(cd.st_device)
            client.SetVolumeLevel(level)
            logger.info(f"Set volume to {level} on device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting volume on device {device_id}: {e}")
            return False

    def toggle_mute(self, device_id: str) -> bool:
        """Toggle mute on a device."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return False
        try:
            client = SoundTouchClient(cd.st_device)
            client.Mute()
            logger.info(f"Toggled mute on device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error toggling mute on device {device_id}: {e}")
            return False
