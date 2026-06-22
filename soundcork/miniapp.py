"""
Endpoints for a miniapp UI.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from soundcork.constants import DEFAULT_DATESTR, DEFAULT_DEVICE_IMAGE, DEVICE_IMAGE_MAP
from soundcork.datastore import DataStore
from soundcork.marge import add_group as marge_add_group
from soundcork.model import Preset as PresetModel
from soundcork.ui.speakers import Speakers

if TYPE_CHECKING:
    from soundcork.model import Preset

logger = logging.getLogger(__name__)

EDITABLE_SOURCES = [
    ("INTERNET_RADIO", "Internet Radio"),
    ("TUNEIN", "TuneIn"),
    ("STORED_MUSIC", "Local Media"),
    ("SPOTIFY", "Spotify"),
    ("AMAZON", "Amazon Music"),
    ("DEEZER", "Deezer"),
]

VOLUME_STEP = 5

# How long the dashboard shows a pending action while waiting for the device to
# confirm. Bose TuneIn streams can take 10-20s to buffer; the dashboard JS polls
# /miniapp/status during this window.
PENDING_ACTION_MAX_AGE_SECONDS = 30


def encode_cookie_value(value: object) -> str:
    """Encode text for Set-Cookie's latin-1 constrained header value."""
    return quote(str(value), safe="")


def decode_cookie_value(value: str | None, default: str | None = None) -> str | None:
    if value is None:
        return default
    return unquote(value)


def get_device_image(product_code: str) -> str:
    """Map product code to device image file."""
    return DEVICE_IMAGE_MAP.get(
        (product_code or "").strip().lower(), DEFAULT_DEVICE_IMAGE
    )


def _read_pending_action(
    cookie_value: str | None,
    is_playing: bool,
    current_source: str | None = None,
) -> tuple[str | None, bool]:
    """Parse the pending-action cookie and decide if the device state matches.

    Cookie format:
        play:<ts>            — resolves when is_playing becomes True
        stop:<ts>            — resolves when is_playing becomes False
        source-<NAME>:<ts>   — resolves when the device's source matches NAME
                               (e.g. BLUETOOTH, AUX). is_playing is ignored;
                               for these local inputs we can't assume audio
                               actually starts.

    Returns:
        (pending_action, resolved): pending_action is "play" | "stop" |
        "source" if the device state has NOT yet caught up; None otherwise.
        resolved is True when the cookie can be deleted (state matches or
        cookie is stale/invalid).
    """
    if not cookie_value or ":" not in cookie_value:
        return None, False
    action, _, ts_str = cookie_value.partition(":")
    try:
        ts = int(ts_str)
    except ValueError:
        return None, True
    age = int(time.time()) - ts
    if age > PENDING_ACTION_MAX_AGE_SECONDS:
        return None, True  # stale — clear it

    if action.startswith("source-"):
        target = action.split("-", 1)[1].upper()
        if (current_source or "").upper() == target:
            return None, True  # source caught up — clear it
        return "source", False

    if action in {"play", "stop"}:
        expected_playing = action == "play"
        if is_playing == expected_playing:
            return None, True  # state caught up — clear it
        return action, False

    return None, True


def get_miniapp_router(datastore: DataStore, speakers: Speakers):
    templates = Jinja2Templates(directory="templates")
    templates.env.globals["current_year"] = lambda: datetime.now().year

    router = APIRouter(tags=["miniapp"])

    @router.get("/miniapp", response_class=HTMLResponse)
    async def main_page(request: Request):
        """Redirect to login or dashboard based on session."""
        account_id = request.cookies.get("soundcork_account_id")
        if account_id and datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        else:
            return RedirectResponse(url="/miniapp/login", status_code=303)

    @router.get("/miniapp/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        """Display login page with account selection."""
        try:
            account_ids = datastore.list_accounts()
            accounts_data = {}

            for account_id in account_ids:
                if account_id:
                    try:
                        label = datastore.get_account_info(account_id)
                        device_count = len(datastore.list_devices(account_id))
                        accounts_data[account_id] = {
                            "label": label,
                            "device_count": device_count,
                        }
                    except Exception as e:
                        logger.error(
                            f"Error getting info for account {account_id}: {e}"
                        )
                        continue

            logger.info(f"Rendering login with {len(accounts_data)} accounts")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"accounts": accounts_data, "error": None},
            )
        except Exception as e:
            logger.error(f"Error rendering login page: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"accounts": {}, "error": "Error loading accounts"},
            )

    @router.post("/miniapp/login")
    async def login_submit(request: Request):
        """Handle account selection and set cookie."""
        try:
            form_data = await request.form()
            account_id_raw = form_data.get("account_id")

            if not account_id_raw or not isinstance(account_id_raw, str):
                return RedirectResponse(
                    url="/miniapp/login?error=No account selected", status_code=303
                )

            account_id: str = account_id_raw

            # Verify account exists
            if not datastore.account_exists(account_id):
                return RedirectResponse(
                    url="/miniapp/login?error=Invalid account", status_code=303
                )

            # Get account label
            account_label = datastore.get_account_info(account_id)

            # Create response with redirect
            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # Set cookies for account
            response.set_cookie(
                key="soundcork_account_id",
                value=account_id,
                max_age=86400 * 30,  # 30 days
                httponly=True,
                samesite="strict",
            )
            response.set_cookie(
                key="soundcork_account_label",
                value=encode_cookie_value(account_label),
                max_age=86400 * 30,
                httponly=False,  # Allow JS to read for display
                samesite="strict",
            )

            logger.info(f"User logged in to account {account_id}")
            return response

        except Exception as e:
            logger.error(f"Error during login: {e}")
            return RedirectResponse(
                url="/miniapp/login?error=Login failed", status_code=303
            )

    @router.get("/miniapp/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request):
        """Display dashboard with devices and presets."""
        account_id = ""
        try:
            # Get account from cookie
            account_id = request.cookies.get("soundcork_account_id", "")

            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            # Always fetch the current label from the datastore so renames
            # made via /admin are reflected without re-logging in.
            try:
                account_label = datastore.get_account_info(account_id)
            except Exception:
                account_label = (
                    decode_cookie_value(
                        request.cookies.get("soundcork_account_label"),
                        "Unknown Account",
                    )
                    or "Unknown Account"
                )

            # Verify account still exists
            if not datastore.account_exists(account_id):
                response = RedirectResponse(url="/miniapp/login", status_code=303)
                response.delete_cookie("soundcork_account_id")
                response.delete_cookie("soundcork_account_label")
                return response

            combined_devices = speakers.all_devices()
            devices: list[dict[str, Any]] = []
            presets: list["Preset"] = []

            for device_id in datastore.list_devices(account_id):
                if device_id is None:
                    continue
                try:
                    device_info = datastore.get_device_info(account_id, device_id)
                    cd = combined_devices.get(device_id)

                    # Determine device status with detailed logging for debugging
                    if not cd:
                        status = "not_discovered"
                        logger.info(
                            f"Device {device_id} ({device_info.name}): not_discovered "
                            f"(not in combined_devices)"
                        )
                    elif not cd.online:
                        status = "offline"
                        logger.info(
                            f"Device {device_id} ({device_info.name}): offline "
                            f"(not currently discovered on network)"
                        )
                    elif not cd.in_soundcork:
                        status = "offline"
                        logger.info(
                            f"Device {device_id} ({device_info.name}): offline "
                            f"(not in_soundcork, needs configuration)"
                        )
                    elif cd.marge_server != "Soundcork":
                        status = "online_bose"
                        logger.info(
                            f"Device {device_id} ({device_info.name}): online_bose "
                            f"(discovered but still using Bose: {cd.marge_server})"
                        )
                    else:
                        status = "online"
                        logger.info(
                            f"Device {device_id} ({device_info.name}): online "
                            f"(ready to use)"
                        )

                    devices.append(
                        {
                            "name": device_info.name,
                            "product_code": device_info.product_code,
                            "device_id": device_info.device_id,
                            "status": status,
                            "image_file": get_device_image(device_info.product_code),
                        }
                    )

                    if not presets:
                        try:
                            presets = datastore.get_presets(account_id)
                        except Exception as e:
                            logger.warning(
                                f"Error getting presets for device {device_id}: {e}"
                            )

                except Exception as e:
                    logger.error(f"Error getting device info for {device_id}: {e}")
                    continue

            logger.info(
                f"Rendering dashboard for account {account_id} with {len(devices)} devices and {len(presets)} presets"
            )

            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            # Cookie-backed resume target for the Play button when device is idle
            resume_content_id = request.cookies.get(
                "soundcork_selected_content_item_id"
            )

            # One up-front parallel port-8090 probe across every online device
            # (including the selected one). Populates the unreachable cache
            # so subsequent speaker reads — selected-device get_volume /
            # get_now_playing and the multi-room/power batch calls below —
            # all short-circuit dead hosts instead of paying the connect
            # timeout once each.
            online_ids = [d["device_id"] for d in devices if d["status"] == "online"]
            if online_ids:
                speakers.probe_reachability(online_ids)

            # Pull live state from the selected device (no cookie state for these)
            volume = None
            now_playing = None
            if selected_device_id:
                volume = speakers.get_volume(selected_device_id)
                now_playing = speakers.get_now_playing(selected_device_id)

            # Multi-room zone state for every online device (parallel queries).
            zone_map = speakers.get_all_zones(online_ids) if online_ids else {}
            power_state_map = (
                speakers.get_all_power_states(online_ids) if online_ids else {}
            )
            for d in devices:
                d["power_on"] = power_state_map.get(d["device_id"], False)
                d["is_st10"] = (d.get("product_code") or "").startswith("SoundTouch 10")
            id_to_name = {d["device_id"]: d["name"] for d in devices}

            # Stereo-pair state. Each ST10 either belongs to a stored Group
            # or is a candidate for a new one. We compute:
            #   - device.stereo_pair: { group_id, role, partner_id,
            #     partner_name } when this device is half of an active pair.
            #   - device.stereo_pair_candidates: list of (id, name) of other
            #     online unpaired ST10s the user could pair this one with.
            try:
                stereo_groups = datastore.list_groups(account_id)
            except Exception as e:
                logger.warning(f"list_groups failed: {e}")
                stereo_groups = []
            paired_device_ids: set[str] = set()
            stereo_partner: dict[str, dict[str, str]] = {}
            for g in stereo_groups:
                paired_device_ids.add(g.left_id)
                paired_device_ids.add(g.right_id)
                stereo_partner[g.left_id] = {
                    "group_id": g.id,
                    "role": "LEFT",
                    "partner_id": g.right_id,
                    "partner_name": id_to_name.get(g.right_id, g.right_id),
                }
                stereo_partner[g.right_id] = {
                    "group_id": g.id,
                    "role": "RIGHT",
                    "partner_id": g.left_id,
                    "partner_name": id_to_name.get(g.left_id, g.left_id),
                }
            unpaired_online_st10s = [
                d
                for d in devices
                if d.get("is_st10")
                and d["status"] == "online"
                and d["device_id"] not in paired_device_ids
            ]
            for d in devices:
                d["stereo_pair"] = stereo_partner.get(d["device_id"])
                if (
                    d.get("is_st10")
                    and d["status"] == "online"
                    and d["device_id"] not in paired_device_ids
                ):
                    d["stereo_pair_candidates"] = [
                        (other["device_id"], other["name"])
                        for other in unpaired_online_st10s
                        if other["device_id"] != d["device_id"]
                    ]
                else:
                    d["stereo_pair_candidates"] = []

            # Prefer the live datastore name over the soundcork_selected_device
            # cookie, which is stale whenever the device has been renamed
            # since the user picked it. Cookie remains as a fallback in case
            # the device is no longer in the account.
            if selected_device_id and selected_device_id in id_to_name:
                selected_device = id_to_name[selected_device_id]

            # Each speaker's GetZoneStatus is inconsistent: a master usually
            # lists every slave, but a slave often only lists itself (Bose
            # firmware quirk). Union all responses, keyed by master_device_id,
            # so every member sees every other member as a peer.
            zone_full_members: dict[str, set[str]] = {}
            for did, zd in zone_map.items():
                bucket = zone_full_members.setdefault(zd["master_device_id"], set())
                bucket.add(did)
                bucket.add(zd["master_device_id"])
                for m in zd["members"]:
                    if m["device_id"]:
                        bucket.add(m["device_id"])

            for d in devices:
                z = zone_map.get(d["device_id"])
                if not z:
                    d["zone"] = None
                    continue
                master_id = z["master_device_id"]
                full = zone_full_members.get(master_id, set())
                peer_ids = sorted(mid for mid in full if mid != d["device_id"])
                d["zone"] = {
                    "is_master": z["is_master"],
                    "master_device_id": master_id,
                    "master_name": id_to_name.get(master_id),
                    "peer_ids": peer_ids,
                    "peer_names": [id_to_name.get(mid, mid) for mid in peer_ids],
                }

            selected_content_item = (
                now_playing.get("content_name") if now_playing else None
            )
            is_playing_bool = bool(now_playing and now_playing["is_playing"])
            is_playing = "true" if is_playing_bool else "false"

            # Pending-action handling: if user just clicked play/stop/source
            # and the device hasn't transitioned yet, auto-refresh the dashboard.
            pending_action, pending_resolved = _read_pending_action(
                request.cookies.get("soundcork_pending_action"),
                is_playing_bool,
                now_playing.get("source") if now_playing else None,
            )
            template_response = templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "devices": devices,
                    "presets": presets,
                    "selected_content_item": selected_content_item,
                    "selected_device": selected_device,
                    "selected_device_id": selected_device_id,
                    "is_playing": is_playing,
                    "volume": volume,
                    "now_playing": now_playing,
                    "resume_content_id": resume_content_id,
                    "pending_action": pending_action,
                    "error": None,
                },
            )
            if pending_resolved:
                template_response.delete_cookie("soundcork_pending_action")
            return template_response

        except Exception as e:
            logger.error(f"Error rendering dashboard: {e}")

            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "account_id": account_id,
                    "account_label": "Unknown",
                    "devices": [],
                    "presets": [],
                    "selected_content_item": None,
                    "selected_device": selected_device,
                    "selected_device_id": selected_device_id,
                    "is_playing": "false",
                    "volume": None,
                    "now_playing": None,
                    "resume_content_id": None,
                    "pending_action": None,
                    "error": "Error loading dashboard data",
                },
            )

    @router.post("/miniapp/select-content-item")
    async def select_content_item(request: Request):
        """Handle content_item selection and set cookie."""
        try:
            form_data = await request.form()
            content_item_id = form_data.get("content_item_id")
            content_item_name = form_data.get("content_item_name")

            if (
                not isinstance(content_item_id, str)
                or not isinstance(content_item_name, str)
                or not content_item_id
                or not content_item_name
            ):
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            response.set_cookie(
                key="soundcork_selected_content_item_name",
                value=encode_cookie_value(content_item_name),
                max_age=86400 * 30,  # 30 days
                httponly=False,
                samesite="strict",
            )
            response.set_cookie(
                key="soundcork_selected_content_item_id",
                value=content_item_id,
                max_age=86400 * 30,  # 30 days
                httponly=False,
                samesite="strict",
            )

            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            if selected_device_id:
                success = speakers.play_content_item(
                    selected_device_id, content_item_id
                )
                if success:
                    response.set_cookie(
                        key="soundcork_pending_action",
                        value=f"play:{int(time.time())}",
                        max_age=PENDING_ACTION_MAX_AGE_SECONDS,
                        httponly=True,
                        samesite="strict",
                    )
                    logger.info(
                        f"Started playback from preset click: content_item {content_item_id} on device {selected_device_id}"
                    )
                else:
                    logger.error("Failed to start playback from preset click")

            return response

        except Exception as e:
            logger.error(f"Error selecting content_item: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/select-source")
    async def select_source(request: Request):
        """Switch the selected device to a local input source (BT, AUX, ...)."""
        try:
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            if not selected_device_id:
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            form_data = await request.form()
            source = str(form_data.get("source", "")).strip().upper()
            source_account = str(form_data.get("source_account", "")).strip()

            if source not in {"BLUETOOTH", "AUX"}:
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # The Bose source registry tags AUX with sourceAccount="AUX"
            # but Bluetooth has no sourceAccount.
            if source == "AUX" and not source_account:
                source_account = "AUX"

            success = speakers.select_source(selected_device_id, source, source_account)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if success:
                response.set_cookie(
                    key="soundcork_pending_action",
                    value=f"source-{source}:{int(time.time())}",
                    max_age=PENDING_ACTION_MAX_AGE_SECONDS,
                    httponly=True,
                    samesite="strict",
                )
            return response

        except Exception as e:
            logger.error(f"Error in select-source endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/select-device")
    async def select_device(request: Request):
        """Handle device selection and set cookie."""
        try:
            form_data = await request.form()
            device_id = form_data.get("device_id")
            device_name = form_data.get("device_name")

            if (
                not isinstance(device_id, str)
                or not isinstance(device_name, str)
                or not device_id
                or not device_name
            ):
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            response.set_cookie(
                key="soundcork_selected_device",
                value=encode_cookie_value(device_name),
                max_age=86400 * 30,  # 30 days
                httponly=False,
                samesite="strict",
            )
            # Also store device_id for future use
            response.set_cookie(
                key="soundcork_selected_device_id",
                value=device_id,
                max_age=86400 * 30,
                httponly=True,
                samesite="strict",
            )
            logger.info(f"Device selected: {device_name} ({device_id})")
            return response

        except Exception as e:
            logger.error(f"Error selecting device: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/play")
    async def play(request: Request):
        """Play the selected content_item on the selected device."""
        try:
            # Get content_item and device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            selected_content_item_id = request.cookies.get(
                "soundcork_selected_content_item_id"
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            if not selected_content_item or not selected_device_id:
                logger.warning("Cannot play: content_item or device not selected")
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            logger.info(
                f"content_item: {selected_content_item}, {selected_content_item_id}"
            )

            # Play the content_item
            success = speakers.play_content_item(
                selected_device_id, str(selected_content_item_id)
            )

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if success:
                response.set_cookie(
                    key="soundcork_pending_action",
                    value=f"play:{int(time.time())}",
                    max_age=PENDING_ACTION_MAX_AGE_SECONDS,
                    httponly=True,
                    samesite="strict",
                )
                logger.info(
                    f"Started playback: content_item {selected_content_item_id} on device {selected_device_id}"
                )
            else:
                logger.error("Failed to start playback")

            return response

        except Exception as e:
            logger.error(f"Error in play endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/stop")
    async def stop(request: Request):
        """Stop playback on the selected device."""
        try:
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            if not selected_device_id:
                logger.warning("Cannot stop: device not selected")
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # Stop playback
            success = speakers.stop_playback(selected_device_id)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if success:
                response.set_cookie(
                    key="soundcork_pending_action",
                    value=f"stop:{int(time.time())}",
                    max_age=PENDING_ACTION_MAX_AGE_SECONDS,
                    httponly=True,
                    samesite="strict",
                )
                logger.info(f"Stopped playback on device {selected_device_id}")
            else:
                logger.error("Failed to stop playback")

            return response

        except Exception as e:
            logger.error(f"Error in stop endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/volume-up")
    async def volume_up(request: Request):
        """Increase volume on the selected device by VOLUME_STEP."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        current = speakers.get_volume(selected_device_id)
        if current is not None:
            speakers.set_volume(
                selected_device_id, min(100, current["actual"] + VOLUME_STEP)
            )
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/volume-down")
    async def volume_down(request: Request):
        """Decrease volume on the selected device by VOLUME_STEP."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        current = speakers.get_volume(selected_device_id)
        if current is not None:
            speakers.set_volume(
                selected_device_id, max(0, current["actual"] - VOLUME_STEP)
            )
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/mute")
    async def mute_toggle(request: Request):
        """Toggle mute on the selected device."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        speakers.toggle_mute(selected_device_id)
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/group-toggle")
    async def group_toggle(request: Request):
        """Toggle whether `device_id` and `other_id` share a multi-room zone."""
        try:
            form_data = await request.form()
            device_id = str(form_data.get("device_id", "")).strip()
            other_id = str(form_data.get("other_id", "")).strip()
            if device_id and other_id and device_id != other_id:
                speakers.group_toggle(device_id, other_id)
        except Exception as e:
            logger.error(f"Error in group-toggle: {e}")
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/group-leave")
    async def group_leave(request: Request):
        """Remove `device_id` from its current multi-room zone."""
        try:
            form_data = await request.form()
            device_id = str(form_data.get("device_id", "")).strip()
            if device_id:
                speakers.ungroup_device(device_id)
        except Exception as e:
            logger.error(f"Error in group-leave: {e}")
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/stereo-pair")
    async def stereo_pair(request: Request):
        """Stereo-pair two ST10s. Form fields:
        master_id: device id of the left/master ST10
        slave_id:  device id of the right ST10
        """
        try:
            account_id = request.cookies.get("soundcork_account_id", "") or ""
            form_data = await request.form()
            master_id = str(form_data.get("master_id", "")).strip()
            slave_id = str(form_data.get("slave_id", "")).strip()
            if not account_id or not master_id or not slave_id:
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if master_id == slave_id:
                logger.warning("stereo_pair: master == slave, ignoring")
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            master_info = datastore.get_device_info(account_id, master_id)
            slave_info = datastore.get_device_info(account_id, slave_id)
            if not (
                datastore.device_is_groupable(master_info)
                and datastore.device_is_groupable(slave_info)
            ):
                logger.warning(
                    f"stereo_pair: refusing — at least one device is not an ST10"
                )
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # Build the <group> XML and persist via marge.add_group, which
            # assigns the group_id and writes Group_{id}.xml under the account.
            import xml.etree.ElementTree as ET  # local import; rarely used

            g = ET.Element("group")
            ET.SubElement(g, "name").text = f"{master_info.name} + {slave_info.name}"
            ET.SubElement(g, "masterDeviceId").text = master_id
            roles = ET.SubElement(g, "roles")
            left_role = ET.SubElement(roles, "groupRole")
            ET.SubElement(left_role, "deviceId").text = master_id
            ET.SubElement(left_role, "role").text = "LEFT"
            ET.SubElement(left_role, "ipAddress").text = master_info.ip_address
            right_role = ET.SubElement(roles, "groupRole")
            ET.SubElement(right_role, "deviceId").text = slave_id
            ET.SubElement(right_role, "role").text = "RIGHT"
            ET.SubElement(right_role, "ipAddress").text = slave_info.ip_address
            ET.SubElement(g, "senderIPAddress").text = master_info.ip_address
            payload_no_id = '<?xml version="1.0" encoding="UTF-8" ?>' + ET.tostring(
                g, encoding="unicode"
            )
            stored_elem = marge_add_group(datastore, account_id, payload_no_id)
            stored_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                + ET.tostring(stored_elem, encoding="unicode")
            )

            # Push the same XML to both speakers' :8090/addGroup so the
            # firmware actually enters stereo mode.
            async def _push(ip: str) -> tuple[str, int]:
                url = f"http://{ip}:8090/addGroup"
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        url,
                        headers={"Content-Type": "application/xml"},
                        content=stored_xml.encode("utf-8"),
                    )
                    return ip, r.status_code

            results = await asyncio.gather(
                _push(master_info.ip_address),
                _push(slave_info.ip_address),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, BaseException):
                    logger.error(f"stereo_pair: speaker push failed: {r!r}")
                else:
                    ip, status = r
                    logger.info(f"stereo_pair: /addGroup on {ip} -> {status}")
        except Exception as e:
            logger.error(f"stereo_pair failed: {e}")
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/stereo-unpair")
    async def stereo_unpair(request: Request):
        """Tear down a stereo pair. Form field: group_id."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "") or ""
            form_data = await request.form()
            group_id = str(form_data.get("group_id", "")).strip()
            if not account_id or not group_id:
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            group = datastore.get_group(account_id, group_id)
            if not group:
                logger.warning(
                    f"stereo_unpair: group {group_id} not found in account {account_id}"
                )
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # GET :8090/removeGroup on each speaker (Bose spec uses GET here).
            async def _drop(ip: str) -> tuple[str, int]:
                url = f"http://{ip}:8090/removeGroup"
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.get(url)
                    return ip, r.status_code

            results = await asyncio.gather(
                _drop(group.left_ip),
                _drop(group.right_ip),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, BaseException):
                    logger.error(f"stereo_unpair: speaker drop failed: {r!r}")
                else:
                    ip, status = r
                    logger.info(f"stereo_unpair: /removeGroup on {ip} -> {status}")

            try:
                err = datastore.delete_group(account_id, group_id)
                if err:
                    logger.warning(f"stereo_unpair: datastore delete: {err}")
            except Exception as e:
                logger.error(f"stereo_unpair: datastore delete failed: {e}")
        except Exception as e:
            logger.error(f"stereo_unpair failed: {e}")
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/media-play")
    async def media_play_endpoint(request: Request):
        """Resume playback on the device's current source (BT/AirPlay/UPnP)."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        success = speakers.media_play(selected_device_id)
        response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
        if success:
            response.set_cookie(
                key="soundcork_pending_action",
                value=f"play:{int(time.time())}",
                max_age=PENDING_ACTION_MAX_AGE_SECONDS,
                httponly=True,
                samesite="strict",
            )
        return response

    @router.post("/miniapp/media-next")
    async def media_next_endpoint(request: Request):
        """Skip to the next track on the device's current source."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        speakers.media_next(selected_device_id)
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/media-previous")
    async def media_previous_endpoint(request: Request):
        """Skip to the previous track on the device's current source."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        speakers.media_previous(selected_device_id)
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/power")
    async def set_power(request: Request):
        """Power a speaker on (wake from standby) or put it into standby.

        Body fields:
            device_id (str): which speaker to act on.
            state (str): "on" or "standby".
        """
        form_data = await request.form()
        device_id_raw = form_data.get("device_id", "")
        state_raw = form_data.get("state", "")
        device_id = str(device_id_raw).strip() if isinstance(device_id_raw, str) else ""
        state = str(state_raw).strip().lower() if isinstance(state_raw, str) else ""
        if not device_id or state not in ("on", "standby"):
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        speakers.set_power_state(device_id, on=(state == "on"))
        return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.get("/miniapp/status")
    async def status(request: Request) -> JSONResponse:
        """JSON snapshot of the selected device's live state.

        Polled by the dashboard JS so it can update the Now Playing widget,
        volume, and preset highlight when the device's source/track/volume
        changes (e.g., the user picks Bluetooth from the SoundTouch app).
        """
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return JSONResponse({"selected": False})

        volume = speakers.get_volume(selected_device_id)
        now_playing = speakers.get_now_playing(selected_device_id)
        is_playing_bool = bool(now_playing and now_playing["is_playing"])

        pending_action, _ = _read_pending_action(
            request.cookies.get("soundcork_pending_action"),
            is_playing_bool,
            now_playing.get("source") if now_playing else None,
        )

        return JSONResponse(
            {
                "selected": True,
                "selected_device_id": selected_device_id,
                "is_playing": is_playing_bool,
                "content_name": (
                    now_playing.get("content_name") if now_playing else None
                ),
                "artist": (now_playing.get("artist") if now_playing else None),
                "is_local_source": bool(
                    now_playing and now_playing.get("is_local_source")
                ),
                "supports_skip": bool(now_playing and now_playing.get("supports_skip")),
                "supports_local_resume": bool(
                    now_playing and now_playing.get("supports_local_resume")
                ),
                "source": (now_playing.get("source") if now_playing else None),
                "source_label": (
                    now_playing.get("source_label") if now_playing else None
                ),
                "volume": volume,
                "pending_action": pending_action,
                "has_resume": bool(
                    request.cookies.get("soundcork_selected_content_item_id")
                ),
            }
        )

    @router.post("/miniapp/logout")
    async def logout(request: Request):
        """Clear session and redirect to login."""
        response = RedirectResponse(url="/miniapp/login", status_code=303)
        response.delete_cookie("soundcork_account_id")
        response.delete_cookie("soundcork_account_label")
        response.delete_cookie("soundcork_selected_content_item_name")
        response.delete_cookie("soundcork_selected_content_item_id")
        response.delete_cookie("soundcork_selected_device")
        response.delete_cookie("soundcork_selected_device_id")
        response.delete_cookie("soundcork_pending_action")
        logger.info("User logged out")
        return response

    @router.get("/miniapp/presets", response_class=HTMLResponse)
    async def presets_page(request: Request):
        """Display preset management page."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "")

            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            try:
                account_label = datastore.get_account_info(account_id)
            except Exception:
                account_label = (
                    decode_cookie_value(
                        request.cookies.get("soundcork_account_label"),
                        "Unknown Account",
                    )
                    or "Unknown Account"
                )

            if not datastore.account_exists(account_id):
                response = RedirectResponse(url="/miniapp/login", status_code=303)
                response.delete_cookie("soundcork_account_id")
                response.delete_cookie("soundcork_account_label")
                return response

            presets = datastore.get_presets(account_id)
            edit_preset = None

            # Check if editing a preset
            edit_id = request.query_params.get("edit")
            if edit_id:
                for preset in presets:
                    if preset.id == edit_id:
                        edit_preset = preset
                        break

            return templates.TemplateResponse(
                request=request,
                name="presets.html",
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "presets": presets,
                    "edit_preset": edit_preset,
                    "sources": EDITABLE_SOURCES,
                    "error": None,
                },
            )

        except Exception as e:
            logger.error(f"Error rendering presets page: {e}")
            return templates.TemplateResponse(
                request=request,
                name="presets.html",
                context={
                    "account_id": "",
                    "account_label": "Unknown",
                    "presets": [],
                    "edit_preset": None,
                    "sources": EDITABLE_SOURCES,
                    "error": "Error loading presets",
                },
            )

    @router.post("/miniapp/presets/save")
    async def save_preset(request: Request):
        """Add or update a preset."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "")
            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            form_data = await request.form()
            slot = str(form_data.get("slot", "")).strip()
            name = str(form_data.get("name", "")).strip()
            source = str(form_data.get("source", "")).strip()
            location = str(form_data.get("location", "")).strip()
            container_art = str(form_data.get("container_art", "")).strip()

            # Validate inputs
            if not slot or not name or not source or not location:
                return RedirectResponse(
                    url="/miniapp/presets?error=All fields required",
                    status_code=303,
                )

            try:
                slot_num = int(slot)
                if slot_num < 1 or slot_num > 6:
                    return RedirectResponse(
                        url="/miniapp/presets?error=Slot must be 1-6",
                        status_code=303,
                    )
            except ValueError:
                return RedirectResponse(
                    url="/miniapp/presets?error=Slot must be a number",
                    status_code=303,
                )

            # Load current presets
            presets = datastore.get_presets(account_id)

            # Remove existing preset with same slot
            presets = [p for p in presets if p.id != slot]

            # Create new preset with required fields
            now_str = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            new_preset = PresetModel(
                id=slot,
                name=name,
                source=source,
                type="stationurl",
                location=location,
                container_art=container_art,
                created_on=now_str,
                updated_on=now_str,
                source_id=None,  # TuneIn streams don't require source_id
                source_account=None,
                is_presetable="true",
            )
            presets.append(new_preset)

            # Save presets
            datastore.save_presets(account_id, "", presets)
            logger.info(f"Preset saved: slot {slot}, name {name}")
            return RedirectResponse(url="/miniapp/presets", status_code=303)

        except Exception as e:
            logger.error(f"Error saving preset: {e}")
            return RedirectResponse(
                url="/miniapp/presets?error=Failed to save preset",
                status_code=303,
            )

    @router.post("/miniapp/presets/delete")
    async def delete_preset(request: Request):
        """Delete a preset by slot ID."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "")
            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            form_data = await request.form()
            preset_id = str(form_data.get("preset_id", "")).strip()

            if not preset_id:
                return RedirectResponse(url="/miniapp/presets", status_code=303)

            # Load current presets and filter out the one to delete
            presets = datastore.get_presets(account_id)
            presets = [p for p in presets if p.id != preset_id]

            # Save presets
            datastore.save_presets(account_id, "", presets)
            logger.info(f"Preset deleted: {preset_id}")
            return RedirectResponse(url="/miniapp/presets", status_code=303)

        except Exception as e:
            logger.error(f"Error deleting preset: {e}")
            return RedirectResponse(
                url="/miniapp/presets?error=Failed to delete preset", status_code=303
            )

    return router
