# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

soundcork is a FastAPI-based replacement server for Bose SoundTouch devices. It intercepts API calls that would normally go to Bose's servers, whose SoundTouch cloud support ended on May 6, 2026, and serves them locally. The project reverse-engineers the Bose SoundTouch API to allow users to continue using speaker functionality without Bose cloud services.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, pytest, mypy, black, isort

## Development Setup

1. **Create virtual environment:**
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # needed for tests, linting, and typing
   ```

3. **Configuration:**
   - Copy `soundcork/.env.shared` to `soundcork/.env.private` (or create it)
   - Set `BASE_URL` and `DATA_DIR` in `.env.private`
   - Optional settings: `UNHANDLED_LOG_DIR`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`
   - Files can use either `.env.shared` or `.env.private` (private takes precedence)

## Common Commands

**Run development server:**
```bash
cd soundcork
fastapi dev --host 0.0.0.0 main.py
# Server runs on http://127.0.0.1:8000
# API docs available at http://127.0.0.1:8000/docs
```

**Run production server:**
```bash
cd soundcork
fastapi run main.py
```

**Run tests:**
```bash
pytest
# With coverage report (included in default pytest config)
```

**Run tests for specific file:**
```bash
pytest soundcork/tests/test_<module>.py
```

**Run linters (must pass before merging):**
```bash
black --target-version py312 .
isort .
mypy .
```

**Format code (black + isort in one command):**
```bash
black --target-version py312 . && isort .
```

**Build distributable package:**
```bash
pip install build
python -m build
pip install dist/*.whl
```

## Project Architecture

### Core Components

**main.py** — FastAPI application entry point. Sets up routes for the server endpoints:
- **Marge API** (`/marge/*`) — Main speaker control interface (accounts, devices, presets, recents, sources). The speakers call back into these when their state changes — e.g. on rename a speaker PUTs `/marge/streaming/account/{account}/device/{device_id}` while still mid-way through processing the original `POST /name`, so the asyncio event loop must stay free during admin operations that talk to the speaker (see `asyncio.to_thread` usage in `admin.py`).
- **BMX API** (`/bmx/*`) — Music service integration (TuneIn, Pandora, Spotify, custom streams)
- **MiniApp API** (`/miniapp/*`) — End-user dashboard at `/miniapp/dashboard` for playing presets, switching sources (Bluetooth / Aux / AirPlay status), volume, and multi-room grouping
- **Admin API** (`/admin/*`) — Operator-facing UI for adding/removing/repairing speakers
- **Favicon redirect** — `/favicon.ico` 301s to `/static/images/favicon.ico` to silence speaker-side 404 noise

### Key Modules

**datastore.py** — Central data storage layer handling all persistence:
- Stores account/device configuration, presets, recents, sources as XML files in `data_dir`
- Implements `DataStore` class with methods for reading/writing account and device data
- File-based storage in structure: `{data_dir}/{account_id}/` with subdirectories for devices

**marge.py** — Marge API implementation:
- Handles device configuration, presets, source management
- `account_full_xml()`, `presets_xml()`, `recents_xml()` — Return XML responses matching Bose protocol
- Device management: `add_device_to_account()`, `rename_device()`, `update_preset()`
- `rename_device()` returns `<device>` with `<createdOn>`, `<ipaddress>`, `<macaddress>`, `<name>`, `<updatedOn>`. The mac-address echo and a non-epoch `<createdOn>` are load-bearing — without them the speaker firmware silently rolls back the rename. The corresponding route in `main.py` returns **200 OK**, not 201 Created (PUT-on-update semantics; 201 is read by the firmware as "this is a fresh registration").

**bmx.py** — BMX API implementation (music services):
- TuneIn radio: `tunein_navigate_v1()`, `tunein_search_v1()`, `tunein_playback()`
- Custom stream playback: `play_custom_stream()`
- Podcast support via TuneIn integration
- Returns JSON responses with Bose's BMX service protocol

**miniapp.py** — End-user UI router (the dashboard at `/miniapp/dashboard`):
- Login / account picker, preset playback, volume + mute, transport (play/stop/next/prev), source switching (Bluetooth/Aux/AirPlay), Now-Playing widget
- `EDITABLE_SOURCES` constant exposes the subset of sources users can pick when creating presets
- Multi-room grouping: speaker chips with `+` (group) / `×` (ungroup) actions. `peer_ids` is derived by **unioning every online device's `GetZoneStatus` claims** keyed by `master_device_id`, because the Bose firmware's per-device view is asymmetric — a slave often only lists itself, so without cross-referencing the slave's card shows empty peers
- Dashboard JS polls `/miniapp/status` every 3s for live state changes; cookie-driven `soundcork_pending_action` (`play:<ts>` / `stop:<ts>` / `source-<NAME>:<ts>`) is used to bridge the UI through actions that take several seconds to land on the speaker
- Cold-start auto-reload: if the dashboard renders with all speakers offline, JS schedules a single `window.location.reload()` 6s later (Zeroconf service-browser fills `_VerifiedDevices` asynchronously). Bounded by a `sessionStorage` cooldown so it never loops

**spotify_service.py** — Spotify OAuth integration:
- Handles Spotify authentication and token management
- Integrates with BMX API for Spotify playback

**devices.py** — Speaker network operations:
- HTTP helpers: `read_device_info()`, `read_recents()`, `read_presets()`, `set_marge_account()` (POSTs `/setMargeAccount` on the speaker)
- SSH helpers (paramiko + SCP): `override_speaker_config()` (writes `/mnt/nv/OverrideSdkPrivateCfg.xml`), `reboot_speaker()`, `remove_file_from_speaker()`, `read_file_from_speaker_ssh()`
- `add_device_by_ip(hostname, target_account=None)` is the unified "adopt this speaker into soundcork" function — resolves the target account from (1) the speaker's `/info` `<margeAccountUUID>`, (2) the caller-supplied `target_account`, (3) the sole configured account. When falling back to (2) or (3) it pushes the chosen UUID to the speaker via `set_marge_account()` so its NVRAM persists it. This handles the "orphan device" case where the speaker has no UUID
- `addr_is_reachable()` — fast TCP-port-22 reachability probe used by the admin reachability column

**admin.py** — Admin UI router (`/admin/*`):
- Shell-then-fragment rendering: `GET /admin/` returns the page skeleton instantly; the heavy UPnP rescan + reachability checks happen in `GET /admin/devices-fragment` which the page fetches client-side
- All admin actions return `302 → /admin/` but the client-side JS intercepts every form submit and refreshes the fragment in-place instead — so an in-flight reboot countdown on one card survives an action on another card. State is tracked in an `inflightReboots: Map<deviceId, {endTime, message}>`, with a single global tick that re-paints banners after every fragment swap
- Endpoints: `addDevice`, `repairDevice` (back-compat alias for addDevice), `resetDevice` (SSH-remove the override + reboot — escape hatch for orphan state), `switchToSoundcork`, `removeDevice`, `renameDevice`, `renameAccount`
- All blocking speaker calls are wrapped in `await asyncio.to_thread(...)` so the asyncio event loop stays available to serve the speaker's *own* callbacks. Without this, `renameDevice` deadlocked for 60 seconds: the speaker's `/name` handler synchronously calls back into `/marge/.../device/{id}` during the original POST, and if that callback can't be answered the firmware hits its internal Allegro-webserver timeout
- `remove_device` won't delete the datastore row if the SSH override-file delete fails — otherwise the speaker would still be pointed at soundcork while soundcork forgot about it (orphan state)

**ui/speakers.py** — Speaker discovery + control surface used by both miniapp and admin:
- Wraps `bosesoundtouchapi`'s Zeroconf discovery (`_soundtouch._tcp.local.`)
- 30-second discovery cache TTL; `refresh_discovery(force=True)` runs a fresh scan
- `all_devices()` merges datastore-known devices with Zeroconf-verified ones into `CombinedDevice` objects. Devices in the datastore are authoritative for `name` / `account`; orphan-state speakers (in Zeroconf but not the datastore) get `account=""` and surface in the admin "Unassigned / unconfigured" group (or under the single configured account if exactly one exists)
- Multi-room: `get_zone()`, `get_all_zones()`, `group_toggle()`, `_remove_from_zone()`, `ungroup_device()`. `is_master` is computed by comparing `MasterDeviceId == device_id`, *not* trusting the firmware's `senderIsMaster` flag (which can lie on slave responses)
- Volume sync on grouping: when a slave joins a zone, `_sync_zone_volume()` reads the master's volume and pushes the same level to the new slave so the group plays evenly

### Data Structures

**Model classes (model.py)** — Pydantic models for API responses:
- `BoseXMLResponse` — Custom response class for XML with Bose MIME type
- `Service`, `BmxResponse` — BMX service catalog format
- `BmxNavResponse`, `BmxNavSection` — Navigation hierarchy
- `BmxPlaybackResponse` — Now-playing track information
- `ContentItem`, `Preset`, `Recent` — User content items
- `DeviceInfo`, `ConfiguredSource` — Device and source configuration
- `Group` — Multi-room speaker groups

### Configuration

**Settings (config.py)** — Pydantic settings loaded from `.env.shared` / `.env.private`:
- `BASE_URL` — URL where soundcork is accessible from the speakers
- `DATA_DIR` — Local directory for storing account/device data
- `UNHANDLED_LOG_DIR` — Optional directory for raw 404/unhandled request captures
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` — Optional Spotify OAuth integration

## Testing

- **Location:** `soundcork/tests/`
- **Framework:** pytest with coverage enabled by default
- **Current modules tested:** `test_admin.py`, `test_bmx.py`, `test_datastore.py`, `test_datastore_account_info.py`, `test_miniapp.py`, `test_miniapp_dashboard.py`, `test_speakers.py`
- **Coverage:** Reports generated as term-missing output (shows uncovered lines)
- Tests are run in CI on every push/PR to main branch
- Lint workflow runs `black --check`, `isort --check-only`, and `mypy` strictly — these fail the build if drift is detected

## Important Architecture Notes

1. **XML Format** — Soundcork mimics Bose's XML protocol closely. Device responses are often XML; admin API returns both XML and JSON
2. **Account/Device IDs** — Retrieved from speaker's local web server at `http://{speaker_ip}:8090/info`. The `<margeAccountUUID>` may be empty for "orphan" speakers that lost their UUID during a botched remove or after firmware quirks; the admin UI's "Add to Soundcork" path handles this by adopting them into a chosen account and pushing `/setMargeAccount` to persist the UUID in NVRAM
3. **Static Files** — Web UI assets in `soundcork/static/` and `soundcork/templates/`. Templates are Jinja2; the admin grid is rendered as a fragment at `_devices_fragment.html` and swapped in client-side
4. **Media Resources** — Custom icons/assets in `soundcork/media/`
5. **Ports** — Speakers expose a local HTTP API on 8090 (`/info`, `/setMargeAccount`, `/name`, `/volume`, `/getZone`, etc.), SSH on 22, and DLNA on 8091. Soundcork itself listens on 8000 (direct) and 8001 (through the nginx-ETag sidecar)
6. **Override file** — `soundcork/resources/OverrideSdkPrivateCfg.xml.template` is the per-speaker config soundcork SCPs to `/mnt/nv/OverrideSdkPrivateCfg.xml` to redirect the speaker's `margeServerUrl`, `bmxRegistryUrl`, `statsServerUrl`, and `swUpdateUrl` at soundcork

## Security Considerations

- **Network Isolation:** Must run behind a firewall (home network only). See SECURITY.md
- **No Authentication:** Currently no auth required (appropriate for home networks only)
- **SSH/Telnet Access:** Used to configure devices with USB stick containing `remote_services` file; soundcork SSHes in as root (no password) to drop/remove the override config and to reboot
- **Sensitive Data:** Avoid storing credentials in code; use `.env.private`

## Common Development Patterns

- **Response Types:** Use custom `BoseXMLResponse` class for XML endpoints; Pydantic models auto-serialize to JSON. XML responses are wrapped via `bose_xml_str()` which injects `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` (the `standalone="yes"` matters for the firmware's parser)
- **Async vs threadpool:** Endpoints that call blocking speaker libraries (urllib3 / paramiko) must wrap those calls with `await asyncio.to_thread(...)`. The single-worker uvicorn event loop gets pinned otherwise, and the speaker's own callbacks (e.g. the marge `PUT /device/{id}` issued during `POST /name` processing) can't be answered — leading to firmware-side timeouts
- **Error Handling:** `unhandled_exception_handler.py` catches 404s for debugging; the optional `UNHANDLED_LOG_DIR` setting persists raw request bodies for diagnosing missing endpoints
- **Device Discovery:** Zeroconf-based mDNS discovery via `bosesoundtouchapi`'s `SoundTouchDiscovery`. Cached for 30s; the admin "Refresh" button passes `force=true`. The ServiceBrowser keeps populating `_VerifiedDevices` in the background even after the initial 5s scan — late-responding speakers are picked up by subsequent fragment refreshes
- **ETag Support:** Configured via `fastapi-etag` middleware for efficient caching, used by speaker firmware to decide whether to re-fetch e.g. `account_full_xml`
- **Inline-edit pattern:** Account and device rename in the admin UI use a shared `.inline-edit` component (pencil icon → input + check/cancel icons → fetch-driven save + fragment refresh). No full page reloads

## Running via Docker

```bash
docker-compose up
# Includes nginx reverse proxy for ETag handling (see nginx-ETag.conf)
# Data persisted in ./data, logs in ./logs
# Uses host networking so UPnP discovery and speaker callbacks work on the LAN
```

## References

- [GitHub Issues](https://github.com/deborahgu/soundcork/issues) — Feature requests and bug reports
- [Project Milestones](https://github.com/deborahgu/soundcork/milestones) — Roadmap
- [Wiki](https://github.com/deborahgu/soundcork/wiki/) — Additional developer guidelines
- [SECURITY.md](SECURITY.md) — Deployment security requirements
