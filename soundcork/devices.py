"""Device management

Code to interact with Bose SoundTouch UPnP devices. In almost all cases,
these will be the physical SoundTouch speakers, running the SoundTouch
software on a BusyBox system.
"""

import logging
import socket
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from io import BytesIO
from os import unlink
from typing import Optional
from urllib.parse import urlparse

import paramiko
import upnpclient  # type: ignore
from scp import SCPClient, SCPException  # type: ignore

from soundcork.config import Settings
from soundcork.constants import (
    DEFAULT_DATESTR,
    SPEAKER_DEVICE_INFO_PATH,
    SPEAKER_HTTP_PORT,
    SPEAKER_OVERRIDE_SDK_LOCATION,
    SPEAKER_PRESETS_PATH,
    SPEAKER_RECENTS_PATH,
    SPEAKER_SOURCES_FILE_LOCATION,
)
from soundcork.datastore import DataStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

SSH_TIMEOUT_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 10


def _settings() -> Settings:
    return Settings()


def _datastore() -> DataStore:
    return DataStore()


def hostname_for_device(device: upnpclient.upnp.Device) -> str:
    """Given a UPnP device, return hostname/IP

    Raises AttributeError if there's something wrong with the Device object and
    it has no location.
    """
    return urlparse(device.location).hostname  # type: ignore


def read_recents(hostname: str) -> str:
    return read_file_from_speaker_http(hostname, SPEAKER_RECENTS_PATH)


def read_device_info(hostname: str) -> str:
    return read_file_from_speaker_http(hostname, SPEAKER_DEVICE_INFO_PATH)


def read_presets(hostname: str) -> str:
    return read_file_from_speaker_http(hostname, SPEAKER_PRESETS_PATH)


def read_sources(hostname: str) -> str | None:
    """Read the speaker's Sources file over SSH.

    Returns the file contents on success, or None if the SCP read failed so
    callers can distinguish a transient failure from a genuinely empty file
    (and avoid seeding a new account with empty configured sources).
    """
    sources_tmp_file = tempfile.NamedTemporaryFile(delete=False)
    ok = read_file_from_speaker_ssh(
        host=hostname,
        remote_path=SPEAKER_SOURCES_FILE_LOCATION,
        local_path=sources_tmp_file.name,
    )
    sources = sources_tmp_file.read()
    sources_tmp_file.close()
    unlink(sources_tmp_file.name)
    if not ok:
        logger.warning(f"read_sources: SSH read failed for {hostname}")
        return None
    return sources.decode()


def override_speaker_config(host: str) -> bool:
    bytesio = BytesIO()
    with open("resources/OverrideSdkPrivateCfg.xml.template", "r") as file:
        override_xml = file.read()
        override_xml = override_xml.replace("{SC_BASE_URL}", f"{_settings().base_url}")
        bytesio.write(override_xml.encode())
        bytesio.seek(0)
    return write_file_to_speaker(bytesio, host, SPEAKER_OVERRIDE_SDK_LOCATION)


def write_file_to_speaker(payload: BytesIO, host: str, remote_path: str) -> bool:

    logger.debug(f"copying {remote_path} to {host}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=22,
            username="root",
            password="",
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
        )

        transport = ssh.get_transport()
        if transport is None:
            logger.info(f"No SSH transport available to {host}")
            return False
        with SCPClient(transport) as scp:
            scp.putfo(payload, remote_path)
    except (OSError, paramiko.SSHException, SCPException) as e:
        logger.info(f"Error: {e}")
        return False
    finally:
        ssh.close()
    return True


def remove_file_from_speaker(host: str, remote_path: str) -> bool:
    """SSH to the speaker and delete `remote_path`.

    Used to remove `/mnt/nv/OverrideSdkPrivateCfg.xml` so the speaker
    falls back to Bose's original SDK config on the next boot.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=22,
            username="root",
            password="",
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
        )
        # rm -f swallows "missing file" so a no-op succeeds.
        _stdin, stdout, _stderr = ssh.exec_command(f"rm -f {remote_path}")
        rc = stdout.channel.recv_exit_status()
        logger.debug(f"rm {remote_path} on {host} exited {rc}")
        return rc == 0
    except (OSError, paramiko.SSHException) as e:
        logger.info(f"error removing {remote_path} on {host}: {e}")
        return False
    finally:
        ssh.close()


def reboot_speaker(host: str) -> bool:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=22,
            username="root",
            password="",
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
        )
        ssh.exec_command("reboot")
        logger.debug(f"sent reboot to {host}")
        return True
    except (OSError, paramiko.SSHException) as e:
        logger.info(f"error rebooting {host}: {e}")
        return False
    finally:
        ssh.close()


def read_file_from_speaker_ssh(host: str, remote_path: str, local_path: str) -> bool:
    """Read a file from the remote speaker, using ssh.

    Returns True if the file was fetched, False if the SSH/SCP read failed so
    callers can distinguish a transient failure from a genuinely empty file.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=22,
            username="root",
            password="",
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
        )

        transport = ssh.get_transport()
        if transport is None:
            logger.info(f"No SSH transport available to {host}")
            return False
        with SCPClient(transport) as scp:
            scp.get(remote_path, local_path)
        return True
    except (OSError, paramiko.SSHException, SCPException) as e:
        logger.info(f"Error: {e}")
        return False
    finally:
        ssh.close()


def read_file_from_speaker_http(host: str, path: str) -> str:
    """Read a file from the remote speaker, using their HTTP API."""
    url = f"http://{host}:{SPEAKER_HTTP_PORT}{path}"
    logger.info(f"checking {url}")
    try:
        return str(
            urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS).read(), "utf-8"
        )
    except (OSError, TimeoutError, urllib.error.URLError):
        logger.info(f"no result for {url}")
        return ""


def set_marge_account(host: str, account_uuid: str) -> bool:
    """Tell the speaker firmware which marge account it belongs to.

    The speaker stores this in NVRAM and includes it as <margeAccountUUID>
    in /info from then on. Used to repair "orphan" devices that have a
    soundcork override file but no account UUID.
    """
    url = f"http://{host}:{SPEAKER_HTTP_PORT}/setMargeAccount"
    body = f'<setMargeAccount margeAccountUUID="{account_uuid}"/>'.encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/xml"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            status = resp.status
        logger.info(f"setMargeAccount on {host} -> UUID={account_uuid} status={status}")
        return 200 <= status < 300
    except (OSError, TimeoutError, urllib.error.URLError) as e:
        logger.error(f"setMargeAccount failed on {host}: {e}")
        return False


def get_bose_devices() -> list[upnpclient.upnp.Device]:
    """Return a list of all Bose SoundTouch UPnP devices on the network"""
    devices = upnpclient.discover()
    bose_devices = [d for d in devices if "Bose SoundTouch" in d.model_description]
    logger.info("Discovering upnp devices on the network")
    discovered_names = "\n- ".join([b.friendly_name for b in bose_devices])
    logger.info(f"Discovered Bose devices:\n- {discovered_names}")
    return bose_devices


def get_device_by_id(device_id: str) -> Optional[upnpclient.upnp.Device]:
    devices = get_bose_devices()
    for device in devices:
        try:
            info_str = read_device_info(hostname_for_device(device))
            if info_str:
                info_elem = ET.fromstring(info_str)
                if info_elem.attrib.get("deviceID", "") == device_id:
                    return device
        except (AttributeError, ET.ParseError, ValueError) as e:
            logger.debug(f"Could not match device {device_id} against {device}: {e}")
    return None


def show_upnp_devices() -> None:
    """Print a list of devices, specifying reachable ones."""
    devices = get_bose_devices()
    print(
        "Bose SoundTouch devices on your network. Devices currently "
        "configured to allow file copying (eg. that have been setup "
        "with a USB drive) are prefaced with `*`."
    )
    for d in devices:
        reachable = ""
        if is_reachable(d):
            reachable = "* "
        print(f"{reachable}{d.friendly_name}")


def is_reachable(device: upnpclient.upnp.Device) -> bool:
    """Returns true if device is reachable via telnet, ssh, etc."""
    device_address = urlparse(device.location).hostname
    if not device_address:
        return False
    return addr_is_reachable(device_address)


def addr_is_reachable(device_address: str) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)  # Timeout in case of port not open
    try:
        s.connect((device_address, 22))
        return True
    except OSError:
        return False
    finally:
        s.close()


def add_device(
    device: upnpclient.upnp.Device, target_account: str | None = None
) -> bool:
    hostname = hostname_for_device(device)
    return add_device_by_ip(hostname, target_account)


def add_device_by_ip(hostname: str, target_account: str | None = None) -> bool:
    """Add a device to soundcork's datastore.

    Resolves the target account in this order:
    1. The speaker's /info `<margeAccountUUID>`, if non-empty.
    2. The caller-supplied `target_account`.
    3. The sole configured soundcork account, if exactly one is configured.
    4. Otherwise fail.

    When falling back to (2) or (3) the chosen UUID is also pushed to the
    speaker's NVRAM via /setMargeAccount (best-effort — even if that fails
    the soundcork datastore is the authoritative source for marge XML
    responses).
    """
    datastore = _datastore()

    info_xml = read_device_info(hostname)
    if not info_xml:
        logger.warning(f"add_device_by_ip: /info empty on {hostname}")
        return False
    try:
        info_elem = ET.fromstring(info_xml)
    except ET.ParseError as e:
        logger.error(f"add_device_by_ip: /info parse error on {hostname}: {e}")
        return False

    device_id = info_elem.attrib.get("deviceID", "")
    if not device_id:
        logger.warning(f"add_device_by_ip: /info missing deviceID on {hostname}")
        return False

    account_elem = info_elem.find("margeAccountUUID")
    account_id = (account_elem.text if account_elem is not None else None) or ""

    if not account_id:
        # Speaker doesn't know its own account UUID — adopt it into the
        # caller-supplied account, or the only configured one.
        account_id = target_account or ""
        if not account_id:
            real_accounts = [aid for aid in datastore.list_accounts() if aid]
            if len(real_accounts) == 1:
                account_id = real_accounts[0]
        if not account_id:
            logger.warning(
                f"add_device_by_ip: empty margeAccountUUID on {hostname} "
                f"and no fallback account available"
            )
            return False
        # Persist the chosen UUID on the speaker (best-effort).
        set_marge_account(hostname, account_id)

    if not datastore.account_exists(account_id):
        sources = read_sources(hostname)
        if sources is None:
            # SSH sources read failed — abort rather than create the account
            # with empty configured sources (which the speaker can't play).
            logger.error(
                f"add_device_by_ip: could not read sources from {hostname} over "
                f"SSH; aborting so we don't seed account {account_id} empty"
            )
            return False
        recents = read_recents(hostname)
        presets = read_presets(hostname)
        # FIXME get the account email address for this
        add_account(account_id, recents, presets, sources, None, datastore)

    datastore.add_device(
        account_id,
        device_id,
        datastore.device_info_from_device_info_xml(info_elem),
    )
    return True


def default_sources() -> str:
    """Return a minimal but valid Sources.xml string for seeding a new account.

    A blank account created from the admin UI (the zero-account cold start) has
    no speaker to read a real Sources.xml from, so we seed the local sources
    every SoundTouch exposes — AUX and internet radio — in the exact shape
    `DataStore.get_configured_sources()` / the speaker's marge parser expect
    (mirrors `DataStore.save_configured_sources`). Once a device is adopted it
    can re-sync its own richer Sources.xml.
    """
    # (displayName, id, sourceKey type, sourceKey account, secret, secretType)
    defaults = [
        ("AUX IN", "100001", "AUX", "AUX", "", ""),
        ("INTERNET RADIO", "100002", "INTERNET_RADIO", "", "", "token"),
    ]
    sources_root = ET.Element("sources")
    for display_name, source_id, key_type, key_account, secret, secret_type in defaults:
        source_elem = ET.SubElement(sources_root, "source")
        source_elem.attrib["id"] = source_id
        source_elem.attrib["displayName"] = display_name
        source_elem.attrib["secret"] = secret
        source_elem.attrib["secretType"] = secret_type
        key_elem = ET.SubElement(source_elem, "sourceKey")
        key_elem.attrib["type"] = key_type
        key_elem.attrib["account"] = key_account
        ET.SubElement(source_elem, "createdOn").text = DEFAULT_DATESTR
        ET.SubElement(source_elem, "updatedOn").text = DEFAULT_DATESTR
    tree = ET.ElementTree(sources_root)
    ET.indent(tree, space="    ", level=0)
    return ET.tostring(sources_root, encoding="unicode")


def add_account(
    account_id: str,
    recents: str,
    presets: str,
    sources: str,
    account_name: str | None = None,
    datastore: DataStore | None = None,
) -> bool:
    datastore = datastore or _datastore()
    if not datastore.create_account(account_id, label=account_name):
        return False
    datastore.save_presets_xml(account_id, presets)
    datastore.save_recents_xml(account_id, recents)
    datastore.save_configured_sources_xml(account_id, sources)

    return True
