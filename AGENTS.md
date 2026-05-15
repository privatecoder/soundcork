# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `soundcork/`. `soundcork/main.py` boots the FastAPI app and wires the marge, bmx, admin, and miniapp routes. Keep API logic in focused modules such as `bmx.py`, `marge.py`, `groups_service.py`, and `datastore.py`. Network operations against the speakers live in `devices.py` (HTTP + paramiko/SCP helpers). The unified speaker-state surface used by both the miniapp dashboard and the admin UI is `ui/speakers.py`. UI templates and assets live in `soundcork/templates/`, `soundcork/static/`, and `soundcork/media/`. Tests belong in `soundcork/tests/`. Longer technical notes and deployment docs belong in `docs/`.

## Build, Test, and Development Commands
Use Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cd soundcork
fastapi dev --host 0.0.0.0 main.py
pytest
black --target-version py312 .
isort .
mypy .
python -m build
```

`requirements.txt` is runtime-only; install `requirements-dev.txt` before running tests or formatting/type tools. `fastapi dev` starts the local server on `http://127.0.0.1:8000`; `/docs` exposes the OpenAPI UI. `pytest` includes coverage reporting by default from `pyproject.toml`. The lint workflow runs `black --check`, `isort --check-only`, and `mypy` strictly — fix any drift locally before pushing. Docker deployments should use host networking so Zeroconf discovery and speaker callbacks work on the LAN.

## Coding Style & Naming Conventions
Follow Black formatting and isort import order; do not hand-format around them. Use type hints on new or changed Python code. Prefer small, single-purpose functions and keep Bose-compatible response shapes exact, especially for XML endpoints. Use `snake_case` for modules, functions, and variables; use `PascalCase` for classes and Pydantic models.

## Async & Speaker I/O
Endpoints that call blocking speaker libraries (urllib3 / `bosesoundtouchapi` / paramiko / SCP) **must** wrap those calls with `await asyncio.to_thread(...)`. The single uvicorn worker has a single event loop; pinning it during a speaker call prevents soundcork from answering the *speaker's own* synchronous callbacks (e.g. the marge `PUT /streaming/account/.../device/{id}` issued during the speaker's `POST /name` processing). Without this the firmware hits an internal 60s Allegro-webserver timeout and rolls back the change. See `admin.py` for the pattern.

## Marge Response Shape
Marge responses to the speakers are sensitive. When updating them:
- `PUT /marge/streaming/account/{account}/device/{device_id}` returns **200 OK** (update), not 201 Created
- `<device>` rename responses must echo `<macaddress>` back and must NOT emit `<createdOn>1970-01-01T00:00:00.000+00:00</createdOn>` — if the stored createdOn is the `DEFAULT_DATESTR` sentinel, fall back to `updated_on`. The firmware treats these as "marge has no real record for me" and silently rolls back the change
- XML response bodies are wrapped via `bose_xml_str()` which injects `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` — the `standalone="yes"` matters for the firmware's parser

## Multi-room Zones
`is_master` is derived by comparing `MasterDeviceId == device_id`, not by reading the firmware's `senderIsMaster` attribute (which has been observed lying on slave responses). Peer membership is unioned across every online device's `GetZoneStatus` by `master_device_id`, because individual speakers report an incomplete view (a slave often lists only itself). New slaves joining a zone get their volume aligned with the master via `_sync_zone_volume()`.

## Admin Operations & Orphan State
The admin grid renders shell-first; the heavy UPnP rescan + reachability checks are deferred to `GET /admin/devices-fragment`. All admin actions return `302 → /admin/` and the client-side JS swaps the fragment in-place — never relies on a full page reload, so an in-flight reboot countdown on one card survives an action on another. State is tracked in `inflightReboots: Map<deviceId, {endTime, message}>` with a global tick that re-paints banners after every fragment swap.

"Orphan" devices (speaker has soundcork's `/mnt/nv/OverrideSdkPrivateCfg.xml` installed but soundcork's datastore has no record, OR speaker's `/info` has empty `<margeAccountUUID>`) are surfaced under the single configured account when there is one, or under "Unassigned / unconfigured" otherwise. The unified "Add to Soundcork" path in `devices.add_device_by_ip(hostname, target_account=None)` resolves the target as (1) speaker's `/info` margeAccountUUID, (2) caller-supplied `target_account`, (3) the sole configured account — and pushes the chosen UUID to the speaker via `/setMargeAccount` so the orphan state stops being sticky.

`remove_device` will *not* drop the datastore row if the SSH override-file delete fails: leaving the speaker pointed at soundcork with no record on our side is exactly the orphan state we want to avoid. "Reset to Bose" (`POST /admin/resetDevice/{device_id}`) is the escape hatch for any orphan state — it SSH-deletes the override and reboots without touching the datastore.

## Testing Guidelines
Add or update pytest coverage for every behavior change. Place tests in `soundcork/tests/` and name files `test_<module>.py`. Favor targeted unit tests around parsing, datastore behavior, and response generation. Run a focused test file during iteration, for example `pytest soundcork/tests/test_datastore.py`. The current suite covers admin, bmx, datastore, miniapp (login/dashboard), and ui/speakers.

## Commit & Pull Request Guidelines
Recent history favors short imperative subjects, often with a PR reference, for example `Fix miniapp preset playback and cookies (#311)`. Keep commits scoped to one change. Pull requests should explain the behavior change, note any config or protocol impact, link the issue when relevant, and include screenshots for UI changes.

## Security & Configuration Tips
Never commit secrets or local paths. Store overrides in `soundcork/.env.private`; common defaults belong in `.env.shared`. Key settings are `BASE_URL`, `DATA_DIR`, `UNHANDLED_LOG_DIR`, and optional `SPOTIFY_*` OAuth values. `BASE_URL` must be reachable from the speakers, not just the host. Read `SECURITY.md` before changing networking behavior: this service is intended for trusted home-network deployment behind a firewall.
