"""
Endpoints for a miniapp UI.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from soundcork.constants import DEFAULT_DATESTR, DEFAULT_DEVICE_IMAGE, DEVICE_IMAGE_MAP
from soundcork.datastore import DataStore
from soundcork.ui.speakers import Speakers

from soundcork.model import Preset as PresetModel

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


def encode_cookie_value(value: object) -> str:
    """Encode text for Set-Cookie's latin-1 constrained header value."""
    return quote(str(value), safe="")


def decode_cookie_value(value: str | None, default: str | None = None) -> str | None:
    if value is None:
        return default
    return unquote(value)


def get_device_image(product_code: str) -> str:
    """Map product code to device image file."""
    return DEVICE_IMAGE_MAP.get(product_code.lower(), DEFAULT_DEVICE_IMAGE)


def get_miniapp_router(datastore: DataStore, speakers: Speakers):
    templates = Jinja2Templates(directory="templates")

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
            account_label = decode_cookie_value(
                request.cookies.get("soundcork_account_label"), "Unknown Account"
            )

            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            # Verify account still exists
            if not datastore.account_exists(account_id):
                response = RedirectResponse(url="/miniapp/login", status_code=303)
                response.delete_cookie("soundcork_account_id")
                response.delete_cookie("soundcork_account_label")
                return response

            combined_devices = speakers.all_devices()
            devices: list[dict[str, str]] = []
            presets: list["Preset"] = []

            for device_id in datastore.list_devices(account_id):
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

            # Get selected content_item and device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            is_playing = request.cookies.get("soundcork_is_playing", "false")

            return templates.TemplateResponse(
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
                    "error": None,
                },
            )

        except Exception as e:
            logger.error(f"Error rendering dashboard: {e}")

            # Still try to get selected content_item/device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            is_playing = request.cookies.get("soundcork_is_playing", "false")

            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "account_id": account_id,
                    "account_label": "Unknown",
                    "devices": [],
                    "presets": [],
                    "selected_content_item": selected_content_item,
                    "selected_device": selected_device,
                    "selected_device_id": selected_device_id,
                    "is_playing": is_playing,
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
                response.set_cookie(
                    key="soundcork_is_playing",
                    value="true" if success else "false",
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                if success:
                    logger.info(
                        f"Started playback from preset click: content_item {content_item_id} on device {selected_device_id}"
                    )
                else:
                    logger.error("Failed to start playback from preset click")

            return response

        except Exception as e:
            logger.error(f"Error selecting content_item: {e}")
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
                    key="soundcork_is_playing",
                    value="true",
                    max_age=86400 * 30,
                    httponly=False,
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
                    key="soundcork_is_playing",
                    value="false",
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                logger.info(f"Stopped playback on device {selected_device_id}")
            else:
                logger.error("Failed to stop playback")

            return response

        except Exception as e:
            logger.error(f"Error in stop endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

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
        response.delete_cookie("soundcork_is_playing")
        logger.info("User logged out")
        return response

    @router.get("/miniapp/presets", response_class=HTMLResponse)
    async def presets_page(request: Request):
        """Display preset management page."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "")
            account_label = decode_cookie_value(
                request.cookies.get("soundcork_account_label"), "Unknown Account"
            )

            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

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
            slot = form_data.get("slot", "").strip()
            name = form_data.get("name", "").strip()
            source = form_data.get("source", "").strip()
            location = form_data.get("location", "").strip()
            container_art = form_data.get("container_art", "").strip() or ""

            # Validate inputs
            if not slot or not name or not source or not location:
                return RedirectResponse(url="/miniapp/presets?error=All fields required", status_code=303)

            try:
                slot_num = int(slot)
                if slot_num < 1 or slot_num > 6:
                    return RedirectResponse(url="/miniapp/presets?error=Slot must be 1-6", status_code=303)
            except ValueError:
                return RedirectResponse(url="/miniapp/presets?error=Slot must be a number", status_code=303)

            # Load current presets
            presets = datastore.get_presets(account_id)

            # Remove existing preset with same slot
            presets = [p for p in presets if p.id != slot]

            # Create new preset with required fields
            now_str = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', '.000+00:00')
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
            return RedirectResponse(url="/miniapp/presets?error=Failed to save preset", status_code=303)

    @router.post("/miniapp/presets/delete")
    async def delete_preset(request: Request):
        """Delete a preset by slot ID."""
        try:
            account_id = request.cookies.get("soundcork_account_id", "")
            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            form_data = await request.form()
            preset_id = form_data.get("preset_id", "").strip()

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
            return RedirectResponse(url="/miniapp/presets?error=Failed to delete preset", status_code=303)

    return router
