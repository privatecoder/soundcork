"""
Endpoints for an admin UI.

This is a DRAFT version of the admin ui. The display code is not functioning correctly yet, because the device discovery code isn't working correctly. Before it's considered working even for display-only, it will need to have:

- timeouts for device interaction
- error handling, with errors reported on the web page
- guaranteed loading of the page with a status message of some sort after only a few seconds.
"""

import logging
import urllib.parse
from http import HTTPStatus

from bosesoundtouchapi.soundtouchclient import SoundTouchDevice  # type: ignore
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.devices import (
    add_device_by_ip,
    addr_is_reachable,
    override_speaker_config,
    reboot_speaker,
)
from soundcork.ui.speakers import CombinedDevice, Speakers

router = APIRouter(tags=["admin"])

logger = logging.getLogger(__name__)


def _check_base_url(base_url: str, device_ips: list[str]) -> dict:
    """Check if base_url is configured correctly for device-reachability.

    Returns a dict with `status` (ok|warning|error), `message`, and `base_url`.
    """
    if not base_url:
        return {
            "status": "error",
            "base_url": "",
            "message": "base_url is not configured. Set it in docker-compose.yml or .env so devices can reach soundcork.",
        }

    try:
        parsed = urllib.parse.urlparse(base_url)
    except Exception:
        return {
            "status": "error",
            "base_url": base_url,
            "message": f"base_url '{base_url}' is malformed.",
        }

    host = parsed.hostname or ""

    # Warn for hostnames that devices likely cannot resolve
    unresolvable_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "soundcork"}
    if host.lower() in unresolvable_hosts:
        return {
            "status": "error",
            "base_url": base_url,
            "message": (
                f"base_url uses '{host}', which devices on your network cannot resolve. "
                f"Use the host's LAN IP address (e.g., http://192.168.1.x:8001) instead."
            ),
        }

    # If we know device IPs, warn if base_url host is on a different subnet
    if device_ips and _looks_like_ip(host):
        host_subnet = ".".join(host.split(".")[:3])
        device_subnets = {".".join(ip.split(".")[:3]) for ip in device_ips if ip}
        if device_subnets and host_subnet not in device_subnets:
            return {
                "status": "warning",
                "base_url": base_url,
                "message": (
                    f"base_url host '{host}' is on a different subnet from your devices "
                    f"({', '.join(device_subnets)}.x). Devices may not be able to reach it."
                ),
            }

    return {
        "status": "ok",
        "base_url": base_url,
        "message": "base_url looks reachable from the device network.",
    }


def _looks_like_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def get_admin_router(datastore: DataStore, speakers: Speakers, settings: Settings):
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="templates")

    router = APIRouter(tags=["admin"])

    class CombinedAccount(BaseModel):
        id: str
        label: str
        devices: list[CombinedDevice]
        in_soundcork: bool

    def _account_label(account_id: str) -> str:
        try:
            return datastore.get_account_info(account_id)
        except Exception:
            return account_id

    @router.get("/admin/", response_class=HTMLResponse)
    async def admin(request: Request):
        speakers.refresh_discovery()
        combined_devices = speakers.all_devices()

        unassociated_devices = []
        account_ids = datastore.list_accounts()
        accounts = {}

        for account_id in account_ids:
            if account_id:
                account = CombinedAccount(
                    id=account_id,
                    label=_account_label(account_id),
                    devices=[],
                    in_soundcork=True,
                )
                accounts[account_id] = account

        # sort devices from speakers.all_devices() into accounts. also check
        # to see if they are reachable via ssh (which really only matters in
        # an admin context)
        sorted_keys = sorted(combined_devices)
        for key in sorted_keys:
            dev = combined_devices[key]
            # assign to account
            account_id = dev.account
            if account_id:
                found_account = accounts.get(account_id, None)
                if not found_account:
                    found_account = CombinedAccount(
                        id=account_id,
                        label=_account_label(account_id),
                        devices=[],
                        in_soundcork=False,
                    )
                    accounts[account_id] = found_account

                found_account.devices.append(dev)
            else:
                unassociated_devices.append(dev)
            # also check to see if it's available via ssh
            dev.reachable = addr_is_reachable(dev.ip)

        device_ips = [d.ip for d in combined_devices.values() if d.ip]
        base_url_check = _check_base_url(settings.base_url, device_ips)

        return templates.TemplateResponse(
            request=request,
            name="admin/index.html",
            context={"accounts": accounts, "base_url_check": base_url_check},
        )

    @router.post("/admin/switchToSoundcork/{device_id}")
    async def switch_device(device_id: str):
        logger.info(f"switch {device_id} to soundcork")
        combined_device = speakers.all_devices().get(device_id)
        if combined_device:
            st_device = combined_device.st_device
            if st_device:
                hostname = st_device.Host
                success = override_speaker_config(hostname)
                logger.info(
                    f"override speaker config on {hostname} success = {success}"
                )
                reboot = reboot_speaker(hostname)
                logger.info(f"reboot {hostname} result {reboot}")
                speakers.clear_device(device_id)
        return RedirectResponse(
            url=f"/admin/wait/{device_id}/0", status_code=HTTPStatus.FOUND
        )

    @router.get("/admin/wait/{device_id}/{elapsed}")
    async def wait_switch_device(request: Request, device_id: str, elapsed: int):
        logger.debug(f"checking for restart for {{device_id}}")
        # only wait up to 120 seconds
        if elapsed >= 120:
            return RedirectResponse(url=f"/admin/", status_code=HTTPStatus.FOUND)

        combined_device = speakers.all_devices().get(device_id)
        if combined_device:
            st_device = combined_device.st_device
            if st_device:
                return RedirectResponse(url=f"/admin/", status_code=HTTPStatus.FOUND)

        return templates.TemplateResponse(
            request=request,
            name="admin/wait.html",
            context={"elapsed": elapsed, "device_id": device_id},
        )

    @router.post("/admin/addDevice/{device_id}")
    async def add_device_to_soundcork(device_id: str):
        logger.info(f"add device {device_id} to soundcork")
        combined_device = speakers.all_devices().get(device_id)
        if combined_device:
            st_device = combined_device.st_device
            if st_device:
                hostname = st_device.Host
                success = add_device_by_ip(hostname)
                logger.info(f"added account from {hostname} success = {success}")

        return RedirectResponse(url=f"/admin/", status_code=HTTPStatus.FOUND)

    @router.post("/admin/renameAccount/{account_id}")
    async def rename_account(account_id: str, request: Request):
        """Update an account's display label."""
        if not datastore.account_exists(account_id):
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        form_data = await request.form()
        label_raw = form_data.get("label", "")
        label = str(label_raw).strip() if isinstance(label_raw, str) else ""

        if not label:
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        datastore.save_account_info(account_id, label)
        logger.info(f"Renamed account {account_id} to {label!r}")
        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

    return router
