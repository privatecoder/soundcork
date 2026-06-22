# Fork Changelog

This changelog summarizes the fork-specific work in this repository compared
with the source baseline commit `cdb62d2` (`Fix miniapp preset playback and
cookies (#311)`). It covers the 63 fork commits through `2dca10e`.

Range summary: 47 files changed, 6,746 insertions, 2,539 deletions.

## v1.3.0 — Maiden Anchorage

Feature release on top of v1.2.0 (Lucid Channel). Adds an operator path to
bootstrap soundcork from a completely empty state — create the first account
before any speaker has been adopted — plus development-tooling bumps. A
fork-native take on upstream's create-account idea (#302), reviewed by a second
model.

### Admin: first-account bootstrap

- **Create an account from a zero-account cold start.** Until now the admin /
  adoption flow assumed at least one account already existed: with no accounts
  and an orphan/blank speaker (empty `<margeAccountUUID>`), `add_device_by_ip`
  had nothing to adopt the device into and simply failed. The admin page now
  shows a "Create your first account" form — gated to the empty-accounts state
  — that POSTs to a new `/admin/addAccount` route.
- The route validates the account id against `ACCOUNT_RE` (`^\d{1,20}$`),
  rejecting invalid ids without creating any partial state, and reuses the
  existing `devices.add_account` / `datastore.create_account` primitives — the
  same path the device-driven flow uses — seeding presets, recents, and a
  `default_sources()` baseline (`AUX` + internet radio) in the exact
  `Sources.xml` shape the speaker and datastore expect.
- Account creation runs inline (pure datastore, no speaker I/O) and invalidates
  the device-merge cache on success. Once the account exists, the normal
  Add-to-Soundcork path (`add_device_by_ip` → `set_marge_account`) adopts a
  blank/orphan device into it unchanged.
- Implemented fork-native: wired through the existing shell-then-fragment
  refresh (`_devices_fragment.html` kept), and deliberately *not* porting
  upstream's blocking `time.sleep` or experimental telnet/non-rooted debug
  stubs.

### Development tooling

- Bumped `black` 26.3.1 → 26.5.1 and `types-paramiko` → 4.0.0.20260518
  (dev-only; no runtime dependency change). 26.5.1 introduced no formatting
  drift.

### Tests

- New coverage for `default_sources()` validity + round-trip through
  `ConfiguredSource`, and the `addAccount` route (valid id, invalid-id
  rejection, zero-account cold start). `black` / `isort` / `mypy` clean; 76
  passing.

## v1.2.0 — Lucid Channel

Maintenance and hardening release on top of v1.1.0 (Velvet Cascade). No new
features — instead, a two-model code audit swept the core modules for
correctness bugs, crash paths, blocking-I/O regressions, and dead code. Every
fix was implemented by one model and independently reviewed by another, and is
backed by new regression tests.

### Correctness fixes

- **Stereo groups are listed again.** `list_groups()` passed the stored
  filename (`Group_<id>.xml`) straight into `get_group()`, which re-wrapped it
  to `Group_Group_<id>.xml.xml` and never matched — so saved stereo groups
  silently never appeared on the dashboard. It now passes the bare id.
- **No device loss when moving accounts.** `add_device_to_account()` removed
  the device from its old account before writing the new one; a failure
  mid-move left it in neither. It now writes the new row first, and updates in
  place for same-account moves.
- **Malformed speaker XML no longer crashes.** `device_info_from_poweron_xml`
  and `group_from_xml` raised `UnboundLocalError` on partial payloads; they now
  reject cleanly, and a malformed power-on payload no longer risks writing a
  stray `PowerOn.xml`.
- Fixed a preset `KeyError` (empty `source` written but read as a required
  attribute) and a `recents_xml` `ValueError` (unguarded `int(utcTime)`) that
  could take down the whole `account_full_xml` response.
- Fixed Element-truthiness bugs (`if elem:` on childless XML elements) in the
  login parse and TuneIn station/topic handling.
- `save_account_info()`'s dedup guard looked up the wrong key and never fired;
  it now keys by account id.
- `scan_devices` no longer crashes on orphan speakers missing
  `<margeAccountUUID>`.

### Robustness

- **Miniapp no longer pins the event loop.** ~15 blocking urllib3 speaker calls
  in the dashboard, `/status` (polled every 3s), volume, mute, group, media,
  power, and source handlers are now wrapped in `asyncio.to_thread`, matching
  the discipline `admin.py` already used; the dashboard's volume and
  now-playing reads run concurrently.
- TuneIn requests get bounded timeouts; network/timeout failures map to 502/504
  instead of hanging the loop or surfacing as 500s, and empty stream lists are
  guarded against `IndexError`.
- SSH source-read failures now propagate instead of silently adopting a device
  with empty configured sources.
- `_build_accounts` (the heaviest admin path) runs off the event loop.
- `clear_device` uses `dict.pop(key, None)` so a stale key can no longer break
  the admin remove flow.

### Performance

- `all_devices()` is memoized with a short TTL and explicit invalidation on
  discovery refresh / datastore mutation, instead of being rebuilt on every
  call.
- Per-render reachability is probed once and reused across the zone and
  power-state batches — keeping "unknown" devices in the cross-device peer-id
  union — rather than probing three times per dashboard render.

### Cleanup

- Removed nine unused symbols/imports (`get_spotify_user_id`,
  `tunein_search_link`, `SPOTIFY_SCOPES_FULL`, `group_exists`, `device_by_id`,
  `discovery_age_seconds`, an unused `Zone` import, `asynccontextmanager`,
  `DEFAULT_DATESTR`).
- Extracted shared helpers for the duplicated `<recent>` XML builder, the
  miniapp redirect / pending-action blocks, and the speaker batch loops.
- `print()` → logger, `OSError` guards on `save_poweron` / `save_group`, RNG
  import dedup, hoisted mid-module imports, a browse-ribbon off-by-one, and a
  corrected `repair_device` docstring.

### Tests

- 18 new regression tests across datastore, marge, bmx, devices, and speakers,
  plus updated test doubles. `black` / `isort` / `mypy` clean; 71 passing.

## v1.1.0 — Velvet Cascade

Feature release on top of v1.0.0 (Silent Harbor), adding per-device power
control and manual ST10 stereo-pair setup, plus a fix that finally restores
the correct product-family icons in the miniapp dashboard.

### Dashboard

- **Per-device power toggle.** Each online device card has a circular power
  button in the right-side meta cluster, alongside the status badge. Reads
  the current power state from `GetNowPlayingStatus` (parallel queries with
  the same 3-second timeout pattern as the existing zone polling) and wraps
  `PowerOn` / `PowerStandby` from bosesoundtouchapi.
- New `POST /miniapp/power` endpoint driving the toggle.
- The card's right-side area was restructured: status badge pulled out of
  the `<button>`, and now sits in a vertically-centered flex cluster with
  the power button (and, for ST10s, a stereo-pair link indicator) for a
  cleaner visual grouping.

### ST10 stereo-pair setup

- **Manual stereo-pair UI** for SoundTouch 10s, surfaced inline on each
  ST10 card whenever a pair-relevant state exists.
- When a device is currently paired: shows the partner as an active chip
  with `×` to tear down the pair.
- When a device is unpaired and at least one other unpaired ST10 exists:
  shows each candidate as a chip with `+` to pair (LEFT = the card
  clicked from, RIGHT = the candidate).
- New `POST /miniapp/stereo-pair` and `POST /miniapp/stereo-unpair`
  endpoints. Pair: builds the `<group>` XML, persists via `marge.add_group`,
  POSTs the resulting XML to both speakers' `:8090/addGroup`. Unpair: GETs
  `:8090/removeGroup` on each speaker, then drops the datastore row.
- A small chain "link" indicator appears in the meta cluster of any ST10
  with stereo state — muted when unpaired, primary blue when actively
  paired.
- Multi-room chip row is hidden on the RIGHT half of a stereo pair, since
  the SoundTouch firmware funnels multi-room through the stereo master.
- Fixed `datastore.device_is_groupable` so it actually recognises ST10s;
  the previous exact `== "SoundTouch 10"` check never matched real data
  (stored value is the concatenated form `"SoundTouch 10 sm2"`).

### Device images

- Fixed the long-standing bug where every device card rendered with the
  same default image. The bug came from `device_info_from_device_info_xml`
  re-concatenating `<type>` and an empty `<moduleType>` on every reload,
  producing a trailing-space variant (`"SoundTouch 10 sm2 "`) that missed
  every `DEVICE_IMAGE_MAP` key and fell back to `soundtouch-30.png`.
- ST10 now correctly renders as `d9.png`, ST20 as `d1.png`, ST30 as
  `d2.png`.
- `get_device_image` is now defensive against stray whitespace / `None`.

### Dependencies & hygiene

- Bumped `bosesoundtouchapi` 1.0.86 → 1.0.87.
- Added a docs page for speaker setup and recovery flows.
- Black formatting alignment.

## Product Direction

- Repositioned the project as a heavily modified fork focused on keeping Bose
  SoundTouch devices usable after the official Bose cloud API shutdown.
- Rewrote the README and expanded project documentation for local, Docker,
  development, systemd, USB shell-access, rollback, security, Spotify, Radio
  Browser, deployment, and API behavior.
- Added contributor/agent guidance (`AGENTS.md`, `CLAUDE.md`) that documents the
  current architecture, async speaker-call rules, orphan-device handling, and
  testing expectations.

## Admin UI and Device Setup

- Rebuilt `/admin/` into a faster, fragment-loaded UI with cached discovery,
  parallel reachability checks, refresh controls, reboot countdowns, and
  Base URL health warnings.
- Added inline device rename and account rename flows.
- Added repair/adoption handling for orphaned speakers: devices pointed at
  Soundcork but missing from the datastore now remain visible and can be added
  back into a configured account.
- Added **Reset to Bose** for orphaned devices, deleting
  `/mnt/nv/OverrideSdkPrivateCfg.xml` over SSH and rebooting without touching
  Soundcork datastore state.
- Hardened removal so Soundcork no longer deletes a datastore row when it fails
  to remove the speaker override file, avoiding half-configured orphan states.
- Added account selection when adopting a reachable speaker that has no
  `margeAccountUUID` and multiple Soundcork accounts exist.

## Bose/Marge Compatibility

- Fixed speaker rename deadlock by moving blocking speaker calls out of the
  FastAPI event loop with `asyncio.to_thread(...)`.
- Corrected the Marge device update response used during `/name` rename flows:
  returns `200 OK`, echoes `macaddress`, and avoids epoch-like `createdOn`
  values that cause speakers to reject persistent renames.
- Added `/setMargeAccount` repair support so adopted speakers can persist the
  chosen account UUID in speaker NVRAM.
- Added `/marge/streaming/resources/api_versions.xml` and documented route
  ownership and compatibility constraints.
- Improved group-state handling and fixed asymmetric group derivation by using
  unioned peer IDs and better master/member state.

## Miniapp and Dashboard

- Modernized the miniapp dashboard UI with clearer device selection, refreshed
  account/login styling, footer year handling, and improved visual feedback.
- Added live device-state polling so playback state, selected devices, active
  presets, volume, and local source changes update without relying on stale
  cookies.
- Added volume controls, mute behavior, Bluetooth/Aux/AirPlay indicators,
  track/artist display, Next/Previous controls, and smarter Play/Stop display.
- Added source buttons for Bluetooth and Aux, and refined transport visibility
  for local inputs.
- Added fetch-based play/stop/preset/source actions with instant UI feedback and
  spinner handling until the speaker confirms the transition.
- Added miniapp group controls, group panel UI, leave-group diagnostics, and
  volume sync when grouping speakers.

## Preset Management

- Added preset management UI for adding, editing, deleting, and selecting preset
  slots.
- Changed preset slot entry to a 1-6 dropdown.
- Added complete timestamp and preset fields needed for proper playback.
- Improved preset-card readability, selected/playing highlighting, circular
  badges, cold-start auto-reload, and empty/hidden states before device
  selection.

## Device Discovery and Speaker Interaction

- Improved discovery reliability and diagnostics, including a discovery cache,
  forced refresh support, and better logging.
- Removed the standalone `zeroconf_primer.py` experiment.
- Added SSH/HTTP timeouts and narrowed error handling around speaker operations.
- Added helpers for removing files, rebooting speakers, reading speaker state,
  and checking reachability without hanging admin requests.

## Datastore, Config, and Runtime

- Fixed `StopIteration` crashes when `DATA_DIR` is empty or missing.
- Defaulted `DATA_DIR` to `./data` and auto-created required directories.
- Standardized environment variables on `UPPER_SNAKE_CASE`.
- Fixed `UNHANDLED_LOG_DIR` wiring and added raw unhandled-request logging for
  unknown Bose endpoints.
- Added Base URL validation/warnings to catch deployments where speakers cannot
  reach Soundcork.
- Cleaned stale cookies and local state paths after dashboard refactors.

## Docker, Packaging, and CI

- Added Docker `DEV_MODE` and fixed `fastapi dev` working-directory behavior.
- Updated Docker, compose, `.dockerignore`, `.gitignore`, `.env.shared`,
  `pyproject.toml`, package data, systemd example, and requirements split for
  runtime vs development usage.
- Tightened lint workflows to check formatting instead of silently rewriting.
- Fixed mypy typing drift and aligned Python 3.12 expectations.

## Tests

- Added or updated tests for admin behavior, datastore edge cases, miniapp login
  and dashboard rendering, BMX routes, and speaker UI helpers.
- Added coverage for account info persistence, empty datastore handling, admin
  rename behavior, and dashboard filtering of discovered-only devices.

## Commit Reference

- `174f821` Fix StopIteration error when data_dir is empty or doesn't exist
- `314eba8` Improve device discovery reliability, modernize dashboard UI, and add diagnostic logging
- `642178e` Add preset management UI with add, edit, and delete functionality
- `0fdacab` Fix preset table text colors for visibility
- `686bd5a` Change Preset Slot input to dropdown select 1-6
- `d0b40d2` Add timestamps and complete preset fields for proper playback
- `516f193` Add detailed logging for preset playback debugging
- `49df19b` Add DEV_MODE support for Docker development
- `7895ed5` Fix fastapi dev path in docker-entrypoint.sh
- `087c64b` Fix working directory for fastapi dev
- `2884f75` Redesign admin page to match modern dashboard UI
- `d17796b` Add debug logging for device source list
- `9bd20b8` Expand source list logging to show individual sources
- `f47fd7b` Fix unhandled_log_dir env var name
- `2ed8431` Add request logging middleware for debugging
- `233073e` Clean up debug logging, add base_url health check, document base_url
- `e5975c6` Fix skip-link visibility and position
- `cdd2e16` Add volume controls to dashboard
- `8f86c30` Scope admin device-card styles to admin grid only
- `b4a17ba` Show selected device clearly on dashboard
- `1333c95` Show live device state in top bar instead of stale cookies
- `a65d7cc` Remove SELECTED badge from active device
- `5fd1723` Poll device state after play/stop to avoid stale dashboard render
- `60c469f` Make preset names readable with a translucent chip
- `0c38c4a` Auto-refresh + loading UI while device transitions, highlight playing preset
- `42603bb` Add admin UI to rename accounts
- `2b23a66` Match login account row to admin style
- `5b0f04f` Reflect account renames live and gate Rename button on changes
- `0a1db70` Remove skip-link from all pages
- `ae3567e` Add admin panel link to dashboard and login
- `911fdd6` Make /admin/ load instantly via async fragment + cache + parallel probes
- `1d03c12` Drop redundant "Plays on X" subtitle from Presets section
- `2807ea8` Restore Presets subtitle and stretch preset cards to fill grid cells
- `bcdcaf0` Hide preset tiles until a device is selected, center the grid
- `6fb2967` Instant click feedback on play/stop/preset, shorten state-confirm poll
- `6e71853` Switch play/stop/preset to fetch-based submit with instant UI update
- `043dd35` Don't show stale preset when device switched to Bluetooth/Aux/etc.
- `cf5f7fb` Bind submit interceptor to all forms, not just those in `<main>`
- `cf0a1a1` Poll device status every 3s so external source changes reflect quickly
- `f0e126f` Add Bluetooth / Aux source buttons to the dashboard
- `3a86106` Keep spinner up until the device confirms the transition
- `7d08730` Resolve source-switch action on source change, hide Stop on local inputs
- `dc0d969` Only hide transport controls on AUX, keep them for Bluetooth
- `b7a1668` Show track/artist via Bluetooth/AirPlay/UPnP, add Next/Prev, smart Play
- `efe64c0` Add AirPlay status indicator, simplify track display
- `ca52932` Use current year in footer copyright
- `941081c` Require UPPER_SNAKE_CASE env var names
- `7c16673` Default DATA_DIR to ./data and auto-create the directory
- `5dc08c8` Ignore the local datastore directory
- `db83a91` Clean up SoundCork maintenance issues
- `15d209c` Admin polish: favicon route, README rewrite, group/remove/rename UI
- `20245bc` Admin polish: inline pencil-edit, bottom-aligned action buttons
- `62462f4` Group panel: sleeker UI, volume sync, leave-group diagnostics
- `7915a9e` Fix group asymmetry, inline chips, derive is_master correctly
- `b3a0cf1` Align docs and project configuration
- `1d76cc5` Fix CI lint and miniapp typing
- `9ef8d85` Dashboard: top-align columns, restore preset slot badges
- `f20c8e6` Dashboard: circular preset badges, cold-start auto-reload
- `dddeccf` Keep speaker online after admin rename
- `8265658` Fix admin device rename and unblock SSH-heavy admin endpoints
- `16c65f1` Admin: orphan-state hardening + async polish
- `6efd803` Document admin repair and reset flows
- `2dca10e` Refresh CLAUDE.md / AGENTS.md / README.md for current admin + marge flows
