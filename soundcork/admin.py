"""Endpoints for the admin UI."""

import asyncio
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime
from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from soundcork.config import Settings
from soundcork.constants import SPEAKER_OVERRIDE_SDK_LOCATION
from soundcork.datastore import DataStore
from soundcork.devices import (
    add_device_by_ip,
    addr_is_reachable,
    override_speaker_config,
    reboot_speaker,
    remove_file_from_speaker,
)
from soundcork.ui.speakers import CombinedDevice, Speakers

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
        host = parsed.hostname or ""
    except ValueError:
        return {
            "status": "error",
            "base_url": base_url,
            "message": f"base_url '{base_url}' is malformed.",
        }

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
    ) -> tuple[dict[str, "CombinedAccount"], list[str], list[tuple[str, str]]]:
        """Build the {account_id: CombinedAccount} mapping plus device IP list
        plus a list of configured `(account_id, label)` pairs (for the orphan
        Repair-Configuration account picker).

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

        ORPHAN_ACCOUNT_ID = "__unassigned__"
        # When there's exactly one configured account, devices that the
        # speaker can't tell us about (empty margeAccountUUID — typically
        # caused by a botched Remove that left the soundcork override file
        # in place) get attributed to it. The template will show a Repair
        # button for them. With 0 or 2+ accounts we keep them in an
        # Unassigned bucket because we can't safely guess.
        real_accounts = [aid for aid in account_ids if aid]
        single_account = real_accounts[0] if len(real_accounts) == 1 else None

        for key in sorted(combined_devices):
            dev = combined_devices[key]
            account_id = dev.account
            if not account_id and single_account:
                account_id = single_account
            account_id = account_id or ORPHAN_ACCOUNT_ID
            found = accounts.get(account_id)
            if not found:
                if account_id == ORPHAN_ACCOUNT_ID:
                    label = "Unassigned / unconfigured"
                else:
                    label = _account_label(account_id)
                found = CombinedAccount(
                    id=account_id,
                    label=label,
                    devices=[],
                    in_soundcork=False,
                )
                accounts[account_id] = found
            found.devices.append(dev)

        if check_reachability:
            devices_with_ip = [d for d in combined_devices.values() if d.ip]
            if devices_with_ip:
                with ThreadPoolExecutor(
                    max_workers=min(8, len(devices_with_ip))
                ) as pool:
                    futures = {
                        pool.submit(addr_is_reachable, d.ip): d for d in devices_with_ip
                    }
                    try:
                        for future in as_completed(futures, timeout=2):
                            futures[future].reachable = future.result()
                    except TimeoutError:
                        logger.warning("Timed out checking device reachability")
                    finally:
                        for future, device in futures.items():
                            if not future.done():
                                future.cancel()
                                device.reachable = False

        device_ips = [d.ip for d in combined_devices.values() if d.ip]
        configured_accounts = [
            (aid, accounts[aid].label)
            for aid in real_accounts
            if aid in accounts
        ]
        return accounts, device_ips, configured_accounts

    @router.get("/admin/", response_class=HTMLResponse)
    async def admin(request: Request):
        """Render the admin page shell instantly using cached state.

        The slow UPnP rescan and reachability checks are deferred to
        /admin/devices-fragment, which the page fetches client-side.
        """
        accounts, device_ips, _ = _build_accounts(
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
        accounts, _, configured_accounts = _build_accounts(
            refresh=force, check_reachability=True
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/_devices_fragment.html",
            context={
                "accounts": accounts,
                "configured_accounts": configured_accounts,
            },
        )

    @router.post("/admin/switchToSoundcork/{device_id}")
    async def switch_device(device_id: str):
        logger.info(f"switch {device_id} to soundcork")
        combined_device = speakers.all_devices().get(device_id)
        if combined_device:
            st_device = combined_device.st_device
            if st_device:
                hostname = st_device.Host
                # SSH + SCP + reboot are blocking; run them off the event loop
                # so other admin/dashboard/marge requests stay responsive.
                success = await asyncio.to_thread(override_speaker_config, hostname)
                logger.info(
                    f"override speaker config on {hostname} success = {success}"
                )
                reboot = await asyncio.to_thread(reboot_speaker, hostname)
                logger.info(f"reboot {hostname} result {reboot}")
                speakers.clear_device(device_id)
        # The client-side polls /admin/devices-fragment until the device
        # comes back with marge_server == "Soundcork". No standalone wait
        # page anymore.
        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

    def _resolve_target_account(
        form_account: object, real_accounts: list[str]
    ) -> str | None:
        """Pick which soundcork account to attribute a device to.

        Used by addDevice/repairDevice when the speaker can't tell us via
        its own /info margeAccountUUID. Order:
          1. form-supplied account_id (must match a known account).
          2. the sole configured account, if exactly one.
          3. None → caller refuses the operation.
        """
        if isinstance(form_account, str) and form_account.strip():
            candidate = form_account.strip()
            if candidate in real_accounts:
                return candidate
            logger.warning(
                f"unknown account_id={candidate!r} (known: {real_accounts})"
            )
            return None
        if len(real_accounts) == 1:
            return real_accounts[0]
        return None

    @router.post("/admin/addDevice/{device_id}")
    async def add_device_to_soundcork(device_id: str, request: Request):
        """Bring a device into soundcork's datastore.

        Delegates account resolution to `add_device_by_ip`:
        - If the speaker's /info has a margeAccountUUID, that's used.
        - Else if the form supplied `account_id` (sent when the UI rendered
          a multi-account picker), that's used.
        - Else if exactly one configured account exists, that's used.
        - Else the call fails — the UI shouldn't have offered the action.
        """
        logger.info(f"add device {device_id} to soundcork")
        combined_device = speakers.all_devices().get(device_id)
        if not combined_device or not combined_device.st_device:
            logger.warning(f"add device: unknown / unreachable {device_id}")
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        hostname = combined_device.st_device.Host
        real_accounts = [aid for aid in datastore.list_accounts() if aid]
        form_data = await request.form()
        target_account = _resolve_target_account(
            form_data.get("account_id"), real_accounts
        )
        success = await asyncio.to_thread(
            add_device_by_ip, hostname, target_account
        )
        logger.info(f"add device {device_id} on {hostname} success={success}")
        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

    @router.post("/admin/repairDevice/{device_id}")
    async def repair_device(device_id: str, request: Request):
        """Back-compat alias for addDevice. The Add path now handles the
        orphan / no-margeAccountUUID case directly, so this endpoint is
        kept only so older client form posts continue to work.
        """
        return await add_device_to_soundcork(device_id, request)

    @router.post("/admin/resetDevice/{device_id}")
    async def reset_device(device_id: str):
        """Escape hatch for orphan devices: delete soundcork's override file
        from the speaker over SSH and reboot, so it goes back to vanilla
        Bose firmware. Doesn't touch the datastore — orphans aren't in it
        anyway, and we'd never call this on a fully-configured device.
        """
        logger.info(f"reset_device {device_id}")
        combined_device = speakers.all_devices().get(device_id)
        if not combined_device or not combined_device.st_device:
            logger.warning(f"reset_device: unknown device {device_id}")
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        hostname = combined_device.st_device.Host

        def _do_reset() -> bool:
            ok = remove_file_from_speaker(hostname, SPEAKER_OVERRIDE_SDK_LOCATION)
            logger.info(f"reset_device: override delete on {hostname} success={ok}")
            reboot_speaker(hostname)
            return ok

        await asyncio.to_thread(_do_reset)
        speakers.clear_device(device_id)
        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

    @router.post("/admin/renameDevice/{device_id}")
    async def rename_device(device_id: str, request: Request):
        """Rename a SoundTouch speaker — pushes to both the speaker firmware
        and soundcork's stored DeviceInfo.xml."""
        form_data = await request.form()
        new_name_raw = form_data.get("name", "")
        new_name = str(new_name_raw).strip() if isinstance(new_name_raw, str) else ""
        if not new_name:
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        logger.info(f"rename device {device_id} to {new_name!r}")
        combined_device = speakers.all_devices().get(device_id)
        if not combined_device:
            logger.warning(f"rename requested for unknown device {device_id}")
            return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

        # 1. Push the new name to the speaker (if online). The speaker
        #    will start advertising it on its UPnP friendlyName and /info
        #    immediately.
        #
        #    Run the blocking urllib3 SetName in a worker thread — the speaker's
        #    /name handler calls back into soundcork (PUT /marge/.../device/{id})
        #    *while* the response is in flight, so the asyncio event loop must
        #    stay free to serve that marge callback. Without to_thread() the
        #    handler deadlocks until the speaker's 60s internal timeout fires.
        if combined_device.online and combined_device.st_device:
            t0 = time.monotonic()
            logger.info(f"rename_device: calling speakers.rename_device({device_id})")
            ok = await asyncio.to_thread(
                speakers.rename_device, device_id, new_name
            )
            logger.info(
                f"rename_device: speakers.rename_device({device_id}) "
                f"returned {ok} in {time.monotonic() - t0:.2f}s"
            )

        # 2. Update soundcork's stored DeviceInfo so /marge responses use
        #    the new name on the next request from the speaker.
        account_id = combined_device.account
        if account_id and datastore.device_exists(account_id, device_id):
            try:
                device_info = datastore.get_device_info(account_id, device_id)
                device_info.name = new_name
                datastore.save_device_info(device_info, account_id)
                logger.info(
                    f"renamed device {device_id} in account {account_id} to {new_name!r}"
                )
            except Exception as e:
                logger.error(f"datastore save_device_info failed: {e}")

        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

    @router.post("/admin/removeDevice/{device_id}")
    async def remove_device(device_id: str):
        """Remove a device from soundcork.

        Two-step cleanup:
        1. If the device is reachable AND currently using soundcork as its
           Marge server, SSH to it and delete the OverrideSdkPrivateCfg.xml
           override so it falls back to Bose's firmware config, then reboot.
           If the SSH delete fails (network blip, permission issue, …) we
           abort *without* touching the datastore so we don't leave the
           speaker pointed at soundcork with no record on our side (orphan
           state). The user can retry, or hit Reset to Bose to force.
        2. Otherwise (speaker offline / unreachable / already on Bose):
           delete the device entry from soundcork's datastore. The user is
           responsible for the speaker firmware in that case.
        """
        logger.info(f"remove device {device_id} from soundcork")
        combined_device = speakers.all_devices().get(device_id)
        account_id = combined_device.account if combined_device else ""

        if combined_device and combined_device.st_device:
            hostname = combined_device.st_device.Host
            reachable = await asyncio.to_thread(addr_is_reachable, hostname)
            if combined_device.marge_server == "Soundcork" and reachable:
                ok = await asyncio.to_thread(
                    remove_file_from_speaker,
                    hostname,
                    SPEAKER_OVERRIDE_SDK_LOCATION,
                )
                logger.info(f"removed override on {hostname}: success={ok}")
                if not ok:
                    logger.error(
                        f"remove_device {device_id}: SSH override-removal "
                        f"failed; keeping datastore row to avoid an orphan "
                        f"state. Retry, or use Reset to Bose."
                    )
                    return RedirectResponse(
                        url="/admin/", status_code=HTTPStatus.FOUND
                    )
                await asyncio.to_thread(reboot_speaker, hostname)
                speakers.clear_device(device_id)

        if account_id and datastore.device_exists(account_id, device_id):
            try:
                datastore.remove_device(account_id, device_id)
                logger.info(f"deleted device {device_id} from account {account_id}")
            except Exception as e:
                logger.error(f"datastore remove_device failed: {e}")

        return RedirectResponse(url="/admin/", status_code=HTTPStatus.FOUND)

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
