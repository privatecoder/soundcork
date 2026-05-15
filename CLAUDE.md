# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

soundcork is a FastAPI-based replacement server for Bose SoundTouch devices. It intercepts API calls that would normally go to Bose's servers (which are shutting down in February 2026) and serves them locally. The project reverse-engineers the Bose SoundTouch API to allow users to continue using full speaker functionality after Bose shuts down their cloud services.

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
   ```

3. **Configuration:**
   - Copy `soundcork/.env.shared` to `soundcork/.env.private` (or create it)
   - Set `BASE_URL` and `DATA_DIR` in `.env.private`
   - Files can use either `.env.shared` or `.env.private` (private takes precedence)

## Common Commands

**Run development server:**
```bash
fastapi dev soundcork/main.py
# Server runs on http://127.0.0.1:8000
# API docs available at http://127.0.0.1:8000/docs
```

**Run production server:**
```bash
fastapi run soundcork/main.py
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

**main.py** — FastAPI application entry point. Sets up routes for three main server endpoints:
- **Marge API** (`/marge/*`) — Main speaker control interface (presets, devices, sources, recents)
- **BMX API** (`/bmx/*`) — Music service integration (TuneIn, Pandora, custom streams, Spotify)
- **MiniApp API** (`/miniapp/*`) — Group/zone management for multiple speakers

### Key Modules

**datastore.py** — Central data storage layer handling all persistence:
- Stores account/device configuration, presets, recents, sources as XML files in `data_dir`
- Implements `DataStore` class with methods for reading/writing account and device data
- File-based storage in structure: `{data_dir}/{account_id}/` with subdirectories for devices

**marge.py** — Marge API implementation:
- Handles device configuration, presets, source management
- `account_full_xml()`, `presets_xml()`, `recents_xml()` — Return XML responses matching Bose protocol
- Device management: `add_device_to_account()`, `rename_device()`, `update_preset()`

**bmx.py** — BMX API implementation (music services):
- TuneIn radio: `tunein_navigate_v1()`, `tunein_search_v1()`, `tunein_playback()`
- Custom stream playback: `play_custom_stream()`
- Podcast support via TuneIn integration
- Returns JSON responses with Bose's BMX service protocol

**miniapp.py** — Group/zone management:
- Multi-room audio support for speaker grouping
- Manages speaker groups and their states

**spotify_service.py** — Spotify OAuth integration:
- Handles Spotify authentication and token management
- Integrates with BMX API for Spotify playback

**devices.py** — Device discovery and management:
- UPnP-based device discovery via `upnpclient` library
- Methods: `get_bose_devices()`, `add_device()`, `read_device_info()`
- Retrieves device information from speaker's local web server (port 8090)

**admin.py** — Admin UI router:
- Web-based administration interface for speaker configuration
- Located at `/admin` endpoint

**ui/speakers.py** — UI controller for speaker management

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
- `spotify_client_id`, `spotify_client_secret`, `spotify_redirect_uri` — Optional Spotify OAuth
- `unhandled_log_dir` — Optional 404 logging directory for debugging

## Testing

- **Location:** `soundcork/tests/`
- **Framework:** pytest with coverage enabled by default
- **Current modules tested:** `test_bmx.py`, `test_datastore.py`, `test_miniapp.py`
- **Coverage:** Reports generated as term-missing output (shows uncovered lines)
- Tests are run in CI on every push/PR to main branch

## Important Architecture Notes

1. **XML Format** — Soundcork mimics Bose's XML protocol closely. Device responses are often XML; admin API returns both XML and JSON
2. **Account/Device IDs** — Retrieved from speaker's local web server at `http://{speaker_ip}:8090/info`
3. **Static Files** — Web UI assets in `soundcork/static/` and `soundcork/templates/`
4. **Media Resources** — Custom icons/assets in `soundcork/media/`
5. **Port 8090** — Speakers expose a local API on this port (device info, presets, recents)

## Security Considerations

- **Network Isolation:** Must run behind a firewall (home network only). See SECURITY.md
- **No Authentication:** Currently no auth required (appropriate for home networks only)
- **SSH/Telnet Access:** Used to configure devices with USB stick containing `remote_services` file
- **Sensitive Data:** Avoid storing credentials in code; use `.env.private`

## Common Development Patterns

- **Response Types:** Use custom `BoseXMLResponse` class for XML endpoints; Pydantic models auto-serialize to JSON
- **Error Handling:** `unhandled_exception_handler.py` catches 404s for debugging
- **Device Discovery:** Async device enumeration via UPnP; devices may not be immediately reachable
- **ETag Support:** Configured via `fastapi-etag` middleware for efficient caching

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
