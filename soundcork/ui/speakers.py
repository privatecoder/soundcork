import logging
import time

from bosesoundtouchapi.soundtouchclient import (  # type: ignore
    ContentItem as BCContentItem,
    SoundTouchClient,
    SoundTouchDevice,
)
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from pydantic import BaseModel

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.model import ContentItem

logger = logging.getLogger(__name__)
DISCOVERY_TIMEOUT_SECONDS = 5
# Skip a redundant UPnP rescan if one ran this recently. Force a fresh scan
# with refresh_discovery(force=True) when the user explicitly asks for it.
DISCOVERY_CACHE_TTL_SECONDS = 30

# Short post-action poll so snappy devices show their new state on the
# first dashboard render after the redirect. Slow devices (TuneIn streams
# can take 10-20s to buffer) are handled separately via a pending-action
# cookie + meta refresh on the dashboard, so we keep this very short — the
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

    class Config:
        arbitrary_types_allowed = True


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

    def get_now_playing(self, device_id: str) -> dict | None:
        """Get the device's current playback state.

        Returns:
            dict with `content_name` (str|None), `source` (str), `play_state` (str),
            `is_playing` (bool), or None on failure.
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            return None
        try:
            client = SoundTouchClient(cd.st_device)
            np = client.GetNowPlayingStatus()
            play_state = (getattr(np, "PlayStatus", "") or "").upper()
            content_name = (
                getattr(np, "StationName", None)
                or getattr(np, "Track", None)
                or getattr(np, "Artist", None)
            )
            return {
                "content_name": content_name,
                "source": getattr(np, "Source", "") or "",
                "play_state": play_state,
                "is_playing": play_state in {"PLAY_STATE", "BUFFERING_STATE", "PLAY", "BUFFERING"},
            }
        except Exception as e:
            logger.error(f"Error getting now-playing on device {device_id}: {e}")
            return None

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
