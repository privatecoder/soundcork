"""
Endpoints for an admin UI.

This is a DRAFT version of the admin ui. The display code is not functioning correctly yet, because the device discovery code isn't working correctly. Before it's considered working even for display-only, it will need to have:

- timeouts for device interaction
- error handling, with errors reported on the web page
- guaranteed loading of the page with a status message of some sort after only a few seconds.
"""

import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    templates.env.globals["current_year"] = lambda: datetime.now().year

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

    def _build_accounts(
        refresh: bool, check_reachability: bool
    ) -> tuple[dict[str, "CombinedAccount"], list[str]]:
        """Build the {account_id: CombinedAccount} mapping plus device IP list.

        Heavy operations are gated:
        - `refresh=True` forces a fresh UPnP scan (~5s). Otherwise uses cache.
        - `check_reachability=True` does parallel port-22 checks (~1s total).
        """
        if refresh:
            speakers.refresh_discovery(force=True)
        combined_devices = speakers.all_devices()

        account_ids = datastore.list_accounts()
        accounts: dict[str, CombinedAccount] = {}

        for account_id in account_ids:
            if account_id:
                accounts[account_id] = CombinedAccount(
                    id=account_id,
                    label=_account_label(account_id),
                    devices=[],
                    in_soundcork=True,
                )

        for key in sorted(combined_devices):
            dev = combined_devices[key]
            account_id = dev.account
            if account_id:
                found = accounts.get(account_id)
                if not found:
                    found = CombinedAccount(
                        id=account_id,
                        label=_account_label(account_id),
                        devices=[],
                        in_soundcork=False,
                    )
                    accounts[account_id] = found
                found.devices.append(dev)

        if check_reachability:
            # Parallel port-22 checks across all devices (each capped at ~1s).
            devices_with_ip = [
                d for d in combined_devices.values() if d.ip
            ]
            if devices_with_ip:
                with ThreadPoolExecutor(
                    max_workers=min(8, len(devices_with_ip))
                ) as pool:
                    results = pool.map(
                        addr_is_reachable, [d.ip for d in devices_with_ip]
                    )
                    for d, reachable in zip(devices_with_ip, results):
                        d.reachable = reachable

        device_ips = [d.ip for d in combined_devices.values() if d.ip]
        return accounts, device_ips

    @router.get("/admin/", response_class=HTMLResponse)
    async def admin(request: Request):
        """Render the admin page shell instantly using cached state.

        The slow UPnP rescan and reachability checks are deferred to
        /admin/devices-fragment, which the page fetches client-side.
        """
        accounts, device_ips = _build_accounts(
            refresh=False, check_reachability=False
        )
        base_url_check = _check_base_url(settings.base_url, device_ips)
        return templates.TemplateResponse(
            request=request,
            name="admin/index.html",
            context={
                "accounts": accounts,
                "base_url_check": base_url_check,
                "devices_loading": True,
            },
        )

    @router.get("/admin/devices-fragment", response_class=HTMLResponse)
    async def admin_devices_fragment(request: Request):
        """HTML fragment with fresh discovery + reachability data.

        Called by the admin page after initial render so the user sees
        the page instantly instead of waiting ~5-13s for UPnP + SSH probes.
        """
        force = request.query_params.get("force") == "true"
        # On the auto-fetched render, only force a fresh scan if the cache
        # is stale; the user can hit Refresh to override that.
        accounts, _ = _build_accounts(
            refresh=force, check_reachability=True
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/_devices_fragment.html",
            context={"accounts": accounts},
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
